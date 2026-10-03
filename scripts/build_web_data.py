"""Build the static data the UI loads (web/data/). Every number on screen comes from these files.

Run: .venv/bin/python scripts/build_web_data.py [--since 2026-09-28] [--lannr 20]
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw import config, evidence, gbif, geo, rank, skogs, tools  # noqa: E402
from fw.cache import get_json  # noqa: E402
from fw.profile import RINGS  # noqa: E402

WEB = config.ROOT / "web" / "data"
WINDOW_DAYS = 42
COUNTY_NAMES = {"20": "Dalarna", "21": "Gävleborg", "17": "Värmland"}
EN_TYPE = {"Föryngringsavverkning": "regeneration felling"}


def _round_coords(geom: dict, nd: int = 6) -> dict:
    def r(c):
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def _gj(g, simplify_deg: float = 0.00002) -> dict:
    return _round_coords(g.simplify(simplify_deg, preserve_topology=True).__geo_interface__)


def window_close(received: str) -> str:
    return (date.fromisoformat(received) + timedelta(days=WINDOW_DAYS)).isoformat()


def short_date(d: str) -> str:
    return date.fromisoformat(d).strftime("%-d %b")


def headline(d: dict) -> str:
    """The rule that fired, or the agent's override reason when it disagreed with the rule."""
    if d.get("override_reason"):
        text = "Agent: " + d["override_reason"]["text"].split(". ")[0].rstrip(".")
    else:
        text = re.sub(r"(\d)\.0\b", r"\1", d["rubric_hint"]["rule"])
        text = text[0].upper() + text[1:]
    return text if len(text) <= 150 else text[:147].rstrip() + "..."


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def gbif_web_url(api_url: str) -> str:
    """GBIF API search URL -> the equivalent human-readable gbif.org occurrence search page."""
    q = parse_qs(urlparse(api_url).query)
    keep = {"dataset_key": q.get("datasetKey", [""])[0], "geometry": q.get("geometry", [""])[0],
            "year": q.get("year", [""])[0], "has_coordinate": "true", "has_geospatial_issue": "false",
            "occurrence_status": "present"}
    return "https://www.gbif.org/occurrence/search?" + urlencode(keep)


def evidence_index(ids: set[str]) -> dict:
    """id -> {kind, summary, url} for evidence chips."""
    out = {}
    for e in evidence.get(sorted(ids)):
        p, kind = e["payload"], e["kind"]
        url = None
        if kind == "redlisted_species":
            near = p["nearest_record_by_ring"][str(p["smallest_ring_m"])]
            summary = (f"{p['scientific_name']} ({p['swedish_name']}), {p['category']}: nearest record "
                       f"{near['dist_m']} m from the site, ±{near['unc_m']:.0f} m, {near['year']}")
            url = p["record_urls"][0] if p.get("record_urls") else None
        elif kind == "species_ring":
            summary = (f"{'Inside the site' if p['buffer_m'] == 0 else 'Within ' + str(p['buffer_m']) + ' m'}: "
                       f"{p['kept']} records kept, {p['threatened']} threatened species, {p['eligible']} eligible. "
                       f"Link: GBIF search over the 1,050 m fetch area, before distance and uncertainty filters")
            url = gbif_web_url(e["source_url"]) if e["source_url"] else None
        elif kind == "observation_effort":
            ns = p.get("near_site", {}).get("within_250m", {})
            summary = (f"Effort: {p['records']} records within 1000 m (county median {p['county_median']:g}); "
                       f"{ns.get('records', '?')} within 250 m. Link: GBIF search over the 1,050 m fetch area, "
                       f"before distance and uncertainty filters")
            url = gbif_web_url(e["source_url"]) if e["source_url"] else None
        elif kind == "completed_felling":
            summary = f"Completed felling overlap: {p['overlap_pct']}% ({p['overlap_ha']} ha)"
        elif kind == "notification":
            summary = f"Notification {p['beteckn']}: {p['polygon_ha']} ha, received {p['inkomdatum']}, {p['status']}"
        elif kind == "redlist_entry":
            summary = f"Red List 2025 entry: {p['scientific_name']} ({p['category']})"
        else:
            summary = kind
        out[e["id"]] = {"kind": kind, "summary": summary, "url": url}
    return out


def log_events() -> list[dict]:
    p = config.OUT / "log.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def felling_features(site) -> list[dict]:
    """Completed fellings within ~1 km of the site, with geometry, for drawing."""
    minx, miny, maxx, maxy = geo.buffer_4326(site, 1000).bounds
    feats = skogs.query_features(config.COMPLETED_FELLINGS_URL, "1=1", "Beteckn,Avvdatum,Arealha",
                                 geometry=f"{minx},{miny},{maxx},{maxy}", geometryType="esriGeometryEnvelope",
                                 spatialRel="esriSpatialRelIntersects", inSR=4326)
    return [{"type": "Feature", "geometry": _gj(geo.from_geojson(f["geometry"])),
             "properties": {"beteckn": f["properties"].get("Beteckn"),
                            "felled": skogs.epoch_ms_to_date(f["properties"].get("Avvdatum")),
                            "ha": f["properties"].get("Arealha")}}
            for f in feats if f.get("geometry")]


def feed_text(e: dict, prof: dict, dossier: dict | None) -> str:
    """Plain-English feed line for one logged tool call, from the deterministic evidence profile."""
    t, a = e["tool"], e.get("args", {})
    n, f, eff = prof["notification"], prof["felling"], prof["effort"]
    if t == "get_notification":
        return (f"Read the notification: {n['polygon_ha']} ha {EN_TYPE.get(n['avverktyp'], n['avverktyp'])} "
                f"in {n['kommun'].title()}, received {short_date(n['inkomdatum'])}.")
    if t == "check_completed_felling":
        return ("Checked completed fellings: not felled yet." if f["overlap_pct"] == 0 else
                f"Checked completed fellings: {f['overlap_pct']}% of the site already felled.")
    if t == "query_species":
        b = a.get("buffer_m", 0)
        r = prof["rings"][b]
        where = "inside the site" if b == 0 else f"within {b} m"
        red = sum(1 for s in r["redlisted"].values() if s["category"] in ("CR", "EN", "VU", "NT"))
        return (f"Species records {where}: {plural(r['kept'], 'record')}; red-listed species: {red} "
                f"({r['eligible']} site-bound and threatened by forestry).")
    if t == "observation_effort":
        ns = eff["near_site"]
        return (f"Observation effort: {plural(eff['records'], 'record')} within 1000 m (county median "
                f"{eff['county_median']:g}); {ns['within_250m']['records']} within 250 m (median "
                f"{ns['county_median_within_250m']:g}).")
    if t == "redlist_lookup":
        return f"Looked up {a.get('scientific_name', 'a species')} in the Swedish Red List."
    if t == "get_evidence":
        return "Re-read stored evidence."
    if t == "record_finding":
        if e.get("error"):
            return "Dossier rejected by the validator; revising."
        if dossier:
            w = short_date(window_close(n["inkomdatum"]))
            pr = dossier["priority"]
            label = pr.replace("_", "-").lower() if pr in ("UNDER_SURVEYED", "ALREADY_FELLED") else pr
            return (f"Verdict: {label}. Field check before {w}." if pr in ("HIGH", "MEDIUM")
                    else f"Verdict: {label}. Window closes {w}.")
    return t


def site_payload(b: str, d: dict, prof: dict, events: list[dict]) -> dict:
    n = skogs.get_notification(b)
    site = geo.from_geojson(n["geometry"])
    rings = [{"m": 0, "geometry": _gj(site)}] + [{"m": r, "geometry": _gj(geo.buffer_4326(site, r, 5), 0.00005)}
                                                 for r in RINGS[1:]]
    coords = {r["key"]: (r["decimalLongitude"], r["decimalLatitude"]) for r, _ in gbif.records_within(site, 1000)["rows"]}
    pts = []
    for sp in prof["rings"][1000]["redlisted"].values():
        if sp["category"] not in ("CR", "EN", "VU", "NT"):
            continue
        for rec in sp["recs"]:
            if rec["key"] not in coords:
                continue
            lon, lat = coords[rec["key"]]
            pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                        "properties": {"key": rec["key"], "name": sp["scientific_name"], "swedish": sp["swedish_name"],
                                       "category": sp["category"], "eligible": tools.rubric._eligible(sp),
                                       "mobility": sp["mobility"], "unc_m": rec["unc_m"], "year": rec["year"],
                                       "dist_m": rec["dist_m"], "url": gbif.record_url(rec["key"]),
                                       "evidence_id": sp.get("evidence_id")}})
    cited = {i for r in d["reasons"] + d["contradictions"] for i in r["evidence_ids"]}
    cited |= set(d["rubric_hint"]["evidence_ids"]) | set((d.get("override_reason") or {}).get("evidence_ids", []))
    path = [{"tool": e["tool"], "args": {k: v for k, v in e.get("args", {}).items() if k == "buffer_m"},
             "error": e.get("error", False), "ts": e["ts"]} for e in events]
    # display-only tidying of recorded text: "66.0" -> "66", stray trailing quote; the dossier files stay as recorded
    hint = {**d["rubric_hint"], "rule": re.sub(r"(\d)\.0\b", r"\1", d["rubric_hint"]["rule"])}
    return {
        "beteckn": b, "priority": d["priority"], "rubric_hint": hint, "override_reason": d.get("override_reason"),
        "forced": d.get("forced"), "notification": d["notification"], "window_closes": window_close(d["notification"]["inkomdatum"]),
        "reasons": d["reasons"], "contradictions": d["contradictions"], "uncertainties": d["uncertainties"],
        "not_established": d["not_established"], "next_action": d["next_action"].strip().rstrip('"'), "counts": d["counts"],
        "run_id": d.get("run_id"), "model": d.get("model"), "route": d.get("route"), "recorded_at": d.get("recorded_at"),
        "evidence": evidence_index(cited), "path": path,
        "geometry": {"site": _gj(site), "rings": rings, "species": {"type": "FeatureCollection", "features": pts},
                     "fellings": {"type": "FeatureCollection", "features": felling_features(site)}},
    }


def zero_share(lannr: str) -> dict | None:
    p = config.OUT / f"eval_{lannr}.json"
    if not p.exists():
        return None
    ev = json.loads(p.read_text())
    ids = ev["population_ids"]
    none250 = sum(tools._profile(b)["rings"][250]["records"] == 0 for b in ids)
    none_in = sum(tools._profile(b)["rings"][0]["records"] == 0 for b in ids)
    return {"county": COUNTY_NAMES.get(lannr, lannr), "lannr": lannr, "window": ev["window"], "n": len(ids),
            "no_record_within_250m": none250, "share_no_record_within_250m": round(none250 / len(ids), 4),
            "no_record_inside": none_in, "share_no_record_inside": round(none_in / len(ids), 4)}


def evidence_test() -> dict:
    v = json.loads((config.OUT / "validation.json").read_text())
    cmap = {}
    cm = config.OUT / "commit_map_after_rewrite.txt"
    if cm.exists():
        for line in cm.read_text().splitlines()[1:]:
            old, new = line.split()
            cmap[old] = new

    def new_hash(h):
        return cmap.get(h, h)[:7] if h else None

    def card(name, block, primary_k, provenance, extra):
        rows = block["results"]["rubric_hint"]
        prim = next(r for r in rows if r["k"] == primary_k)
        return {"county": name, "n_total": block["n_total"], "excluded": block["excluded_post2016_only"],
                "n_eval": block["n_eval"], "positives": block["positives"], "base_rate": block["base_rate"],
                "primary": {k: prim[k] for k in ("k", "hits", "precision_at_k", "baseline_mean", "baseline_p95",
                                                 "p_value", "lift", "histogram")},
                "secondary": [{k: r[k] for k in ("k", "hits", "precision_at_k", "baseline_mean", "baseline_p95",
                                                 "p_value", "lift")} for r in rows if r["k"] != primary_k],
                "scored_with_commit": new_hash(provenance.get("git_head")), **extra}
    # Gävleborg: two cut-offs were fixed in code before scoring, but neither was named primary in advance

    g = v["all_notifications_hint_only"]
    gk = max(r["k"] for r in g["results"]["rubric_hint"])
    cards = [card("Gävleborg", g, gk, v["provenance"], {"window": v["window"], "pre_registered": False,
                   "note": "Two cut-offs (k = 10 and 10% of N) were fixed before scoring, but neither was named "
                           "primary in advance; treat p as suggestive."})]
    for lannr, r in sorted(v.get("replications", {}).items()):
        cards.append(card(r["county"], r["result"], r["primary_k"], r["provenance"],
                          {"window": r["window"], "pre_registered": True}))
    agent = v["agent_sample"]["results"]
    # Habitat type of the nearest pre-2016 key habitat at each positive sample site (for the footnote)
    biot = []
    for l in v["labels"]:
        if l["label"] == 1:
            pre = sorted((o for o in l["key_habitats_within_250m"] if o["datinv"] and o["datinv"] < "2016-01-01"),
                         key=lambda o: o["distance_m"])
            biot.append(pre[0]["biotop"])
    swamp = sum("sump" in (b or "").lower() for b in biot)
    return {
        "label": v["leakage"]["label"], "draws": v["draws"], "cards": cards,
        "protocol_commit": new_hash(v["provenance"]["git_head"]),
        "agent_sample": {"county": "Gävleborg", "n_sampled": v["agent_sample"]["n_total"],
                         "n_eval": v["agent_sample"]["n_eval"], "positive_biotopes": biot, "positives_swamp_forest": swamp,
                         "positives": v["agent_sample"]["positives"],
                         "agent": {k: agent["agent"][0][k] for k in ("k", "hits", "precision_at_k", "baseline_mean", "p_value", "lift")},
                         "rubric_hint": {k: agent["rubric_hint"][0][k] for k in ("k", "hits", "precision_at_k", "baseline_mean", "p_value", "lift")}},
        "leakage": v["leakage"],
    }


def county_outline(lannr: str) -> dict | None:
    """County outline for the no-tiles fallback, from OpenStreetMap Nominatim (cached to disk)."""
    name = {"20": "Dalarnas län"}.get(lannr)
    if not name:
        return None
    try:
        body = get_json("https://nominatim.openstreetmap.org/search",
                        {"q": name, "format": "geojson", "polygon_geojson": 1, "polygon_threshold": 0.003, "limit": 1})
        f = body["features"][0]
        return {"type": "Feature", "geometry": _round_coords(f["geometry"], 4), "properties": {"name": name}}
    except Exception as e:  # the fallback then shows only the site extent
        print("county outline unavailable:", e)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lannr", default="20")
    ap.add_argument("--since", default="2026-09-28")
    ap.add_argument("--until", default=(date.today() + timedelta(days=1)).isoformat())
    a = ap.parse_args()
    (WEB / "sites").mkdir(parents=True, exist_ok=True)

    notes = skogs.notifications_between(a.lannr, a.since, a.until)
    dossiers = {d["beteckn"]: d for d in rank.load_dossiers([n["beteckn"] for n in notes])}
    missing = [n["beteckn"] for n in notes if n["beteckn"] not in dossiers]
    if missing:
        print(f"warning: {len(missing)} notifications have no dossier yet: {missing[:5]}...")
    events = log_events()
    by_site = defaultdict(list)
    for e in events:
        if e.get("type") == "tool_call":
            d = dossiers.get(e.get("notification"))
            if d and e["run_id"] == d.get("run_id"):
                by_site[e["notification"]].append(e)

    sites, replay = [], []
    order = {d["beteckn"]: i for i, d in enumerate(rank.ranked(list(dossiers)))}
    for n in notes:
        b = n["beteckn"]
        if b not in dossiers:
            continue
        d, prof = dossiers[b], tools._profile(b)
        payload = site_payload(b, d, prof, by_site[b])
        (WEB / "sites" / f"{b.replace(' ', '_')}.json").write_text(json.dumps(payload, ensure_ascii=False))
        sites.append({"beteckn": b, "kommun": n["kommun"], "area_ha": d["notification"]["polygon_ha"],
                      "received": d["notification"]["inkomdatum"], "window_closes": payload["window_closes"],
                      "priority": d["priority"], "rubric_hint": d["rubric_hint"]["priority"],
                      "overridden": d["priority"] != d["rubric_hint"]["priority"], "forced": bool(d.get("forced")),
                      "rank": order[b] + 1, "headline": headline(d),
                      "centroid": d["notification"]["centroid"], "polygon": payload["geometry"]["site"]})
        for e in by_site[b]:
            replay.append({"ts": e["ts"], "run_id": e["run_id"], "beteckn": b, "tool": e["tool"],
                           "error": e.get("error", False), "text": feed_text(e, prof, d),
                           "final": e["tool"] == "record_finding" and not e.get("error")})
    replay.sort(key=lambda r: (r["ts"], r["beteckn"]))
    counts = Counter(s["priority"] for s in sites)
    week = {"county": COUNTY_NAMES[a.lannr], "lannr": a.lannr, "received_from": a.since,
            "received_to": (date.fromisoformat(a.until) - timedelta(days=1)).isoformat(),
            "notified": len(notes), "investigated": len(sites), "counts": dict(counts),
            "overrides": sum(s["overridden"] for s in sites), "window_days": WINDOW_DAYS,
            "sites": sorted(sites, key=lambda s: s["rank"])}
    (WEB / "week.json").write_text(json.dumps(week, ensure_ascii=False))
    (WEB / "replay.json").write_text(json.dumps({"steps": replay, "sites": len(sites)}, ensure_ascii=False))
    (WEB / "evidence_test.json").write_text(json.dumps(evidence_test(), ensure_ascii=False, indent=1))
    shares = [s for s in (zero_share(l) for l in ("21", "17")) if s]
    (WEB / "stats.json").write_text(json.dumps({
        "zero_record_share": shares,
        "headline": ("More than half of newly notified sites have no species record within 250 m since 2016."
                     if shares and all(s["share_no_record_within_250m"] > 0.5 for s in shares) else None)},
        ensure_ascii=False, indent=1))
    cond = config.OUT / "condense.json"
    (WEB / "condense.json").write_text(cond.read_text() if cond.exists() else json.dumps({"status": "pending"}))
    outline = county_outline(a.lannr)
    (WEB / "county.json").write_text(json.dumps(outline or {}, ensure_ascii=False))
    print(f"web/data: {len(sites)} sites, {len(replay)} replay steps, counts {dict(counts)}")


if __name__ == "__main__":
    main()
