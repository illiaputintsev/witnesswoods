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

from fw import config, context, evidence, gbif, geo, rank, skogs, tools  # noqa: E402
from fw.cache import get_json  # noqa: E402
from fw.profile import RINGS  # noqa: E402

WEB = config.ROOT / "web" / "data"
WINDOW_DAYS = 42
COUNTY_NAMES = {"01": "Stockholm", "03": "Uppsala", "04": "Södermanland", "05": "Östergötland", "06": "Jönköping",
                "07": "Kronoberg", "08": "Kalmar", "09": "Gotland", "10": "Blekinge", "12": "Skåne", "13": "Halland",
                "14": "Västra Götaland", "17": "Värmland", "18": "Örebro", "19": "Västmanland", "20": "Dalarna",
                "21": "Gävleborg", "22": "Västernorrland", "23": "Jämtland", "24": "Västerbotten", "25": "Norrbotten"}
RUBRIC_STATUS = {"20": "tuned", "21": "tested", "17": "tested"}  # everywhere else the rubric is applied untested
EN_TYPE = {"Föryngringsavverkning": "regeneration felling"}
EN_STATUS = {"Anmält för avverkning": "notified for felling"}
# Red List organism groups (Organismgrupp1) in plain English
EN_GROUP = {"Lavar": "lichen", "Storsvampar": "fungus", "Mossor": "moss", "Kärlväxter": "vascular plant",
            "Skalbaggar": "beetle", "Fåglar": "bird", "Däggdjur": "mammal", "Fjärilar": "butterfly or moth",
            "Tvåvingar": "fly", "Steklar": "bee, wasp or ant", "Blötdjur": "mollusc", "Spindeldjur": "arachnid",
            "Halvvingar": "true bug", "Sländor": "dragonfly or relative", "Grod- och kräldjur": "amphibian or reptile",
            "Alger": "alga", "Fiskar": "fish", "Kräftdjur": "crustacean", "Mångfotingar": "millipede or centipede",
            "Hopprätvingar": "grasshopper or cricket", "Övriga organismer": "other organism"}
CAT_ORDER = {"CR": 0, "EN": 1, "VU": 2, "NT": 3}


def _round_coords(geom: dict, nd: int = 5) -> dict:
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
    if t == "landscape_context":
        c = context.compute(n["beteckn"])
        fh = c["felled_ha"]
        return (f"Landscape context: {fh['since_2015']:g} ha felled within 1 km since 2015; "
                f"{len(c['records_in_later_fellings'])} red-listed records inside later fellings.")
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
    rings = [{"m": 0, "geometry": _gj(site)}] + [{"m": r, "geometry": _gj(geo.buffer_4326(site, r, 5), 0.0001)}
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
                                       "dist_m": rec["dist_m"],
                                       "evidence_id": sp.get("evidence_id")}})
    cited = {i for r in d["reasons"] + d["contradictions"] for i in r["evidence_ids"]}
    cited |= set(d["rubric_hint"]["evidence_ids"]) | set((d.get("override_reason") or {}).get("evidence_ids", []))
    ctx = context.compute(b)  # computed for the snapshot; this run's agent did not see it
    later = {}
    for x in ctx["records_in_later_fellings"]:
        later.setdefault(x["key"], x["felled"])
    for f in pts:
        f["properties"]["felled_after"] = later.get(f["properties"]["key"])
    ev_index = evidence_index(cited)
    kinds = {i: e["kind"] for i, e in ev_index.items()}
    path = [{"tool": e["tool"], "args": {k: v for k, v in e.get("args", {}).items() if k == "buffer_m"},
             "error": e.get("error", False), "ts": e["ts"]} for e in events]
    # display-only tidying of recorded text: "66.0" -> "66", stray trailing quote; the dossier files stay as recorded
    hint = {**d["rubric_hint"], "rule": re.sub(r"(\d)\.0\b", r"\1", d["rubric_hint"]["rule"])}
    return {
        "kind": "agent",
        "beteckn": b, "priority": d["priority"], "rubric_hint": hint, "override_reason": d.get("override_reason"),
        "forced": d.get("forced"), "notification": d["notification"], "window_closes": window_close(d["notification"]["inkomdatum"]),
        "reasons": d["reasons"], "contradictions": d["contradictions"], "uncertainties": d["uncertainties"],
        "not_established": d["not_established"], "next_action": d["next_action"].strip().rstrip('"'), "counts": d["counts"],
        "run_id": d.get("run_id"), "model": d.get("model"), "route": d.get("route"), "recorded_at": d.get("recorded_at"),
        "evidence": ev_index, "path": path,
        "reason_kinds": [sorted({kinds.get(i, "?") for i in r["evidence_ids"]}) for r in d["reasons"]],
        "species_lines": species_lines(cited, prof), "context": context_block(ctx),
        "geometry": {"site": _gj(site), "rings": rings, "species": {"type": "FeatureCollection", "features": cap_points(pts)},
                     "fellings": {"type": "FeatureCollection", "features": felling_features(site)}},
    }


PHOTOS = {}
PER_SPECIES, MAX_POINTS = 5, 200


def cap_points(pts: list[dict]) -> list[dict]:
    """Map points only: the 8 nearest records per species, at most 400 per site (eligible first)."""
    by = defaultdict(list)
    for f in pts:
        by[f["properties"]["name"]].append(f)
    kept = [f for fs in by.values() for f in sorted(fs, key=lambda f: f["properties"]["dist_m"])[:PER_SPECIES]]
    kept.sort(key=lambda f: (not f["properties"]["eligible"], CAT_ORDER.get(f["properties"]["category"], 9),
                             f["properties"]["dist_m"]))
    return kept[:MAX_POINTS]


def load_photos() -> None:
    p = WEB / "photos.json"
    if p.exists():
        PHOTOS.update({k: v for k, v in json.loads(p.read_text()).items() if v})


def photo_entry(name: str) -> dict | None:
    """Caption in one fixed format: 'Reference photo of the species, not from this site. © author, licence, source.'"""
    ph = PHOTOS.get(name)
    if not ph:
        return None
    a = ph.get("author", "")
    m = re.search(r"\(c\)\s*([^,(]+)", a) or re.search(r"©\s*([^,(]+)", a)
    author = (m.group(1) if m else a).strip() or "unknown author"
    return {"file": ph["file"], "source_url": ph["source_url"],
            "caption": f"Reference photo of the species, not from this site. © {author}, {ph['licence']}, {ph['source']}."}


def species_lines(cited: set, prof: dict) -> list[dict]:
    """Cited red-listed species as 'garnlav, lichen (Alectoria sarmentosa), VU, 184 m', names from the Red List."""
    out = []
    for e in evidence.get(sorted(cited)):
        if e["kind"] != "redlisted_species":
            continue
        p = e["payload"]
        if p["category"] not in CAT_ORDER:
            continue
        near = p["nearest_record_by_ring"][str(p["smallest_ring_m"])]
        sw = p["swedish_name"] if p["swedish_name"] not in ("no data", "", None) else None
        grp = EN_GROUP.get(p["group"], p["group"].lower())
        eligible = tools.rubric._eligible(p)
        out.append({"text": f"{sw + ', ' if sw else ''}{grp} ({p['scientific_name']}), {p['category']}, {near['dist_m']} m",
                    "swedish": sw, "group": grp, "scientific": p["scientific_name"], "category": p["category"],
                    "dist_m": near["dist_m"], "unc_m": near["unc_m"], "year": near["year"], "eligible": eligible,
                    "mobility": p["mobility"], "evidence_id": e["id"], "url": (p.get("record_urls") or [None])[0],
                    "photo": photo_entry(p["scientific_name"])})
    out.sort(key=lambda x: (not x["eligible"], CAT_ORDER[x["category"]], x["dist_m"]))
    return out


def context_block(c: dict) -> dict:
    """Group 'records inside later fellings' by species for display."""
    groups = {}
    for x in c["records_in_later_fellings"]:
        g = groups.setdefault((x["species"], x["felled"]), {**x, "n": 0, "years": set()})
        g["n"] += 1
        g["years"].add(x["record_date"][:4])
    items = [{k: v for k, v in g.items() if k not in ("years",)} | {"record_years": sorted(g["years"])}
             for g in groups.values()]
    items.sort(key=lambda x: (CAT_ORDER.get(x["category"], 9), x["dist_m"]))
    return {"radius_m": c["radius_m"], "felled_ha": c["felled_ha"], "zone_ha": c["zone_ha"],
            "later_fellings": items, "computed": "for this snapshot; the agent run shown here did not see it",
            "note": c["note"]}


def week_share(sites: list[dict]) -> dict:
    none250 = sum(tools._profile(s["beteckn"])["rings"][250]["records"] == 0 for s in sites)
    return {"n": len(sites), "no_record_within_250m": none250, "share_no_record_within_250m": round(none250 / len(sites), 4)}


RULE_NOT_ESTABLISHED = [
    "No agent has reviewed this site: this is the deterministic rubric applied to the evidence profile.",
    "Absence of records is not absence of species.",
    "A species record shows the species was recorded nearby, not that it occupies the site today.",
    "Whether the stand has high ecological value, or whether felling is legal or appropriate.",
]


def ref_basis(lannr: str, eff: dict) -> str:
    from fw import effort as _effort
    return _effort.county_reference(lannr).get("basis") or eff["reference"]


def ref_word(lannr: str) -> str:
    from fw import effort as _effort
    return "national week median" if str(_effort.county_reference(lannr).get("basis", "")).startswith("national") else "county median"


def rule_payload(b: str, prof: dict, lannr: str) -> dict:
    """Rule-only profile for a site no agent has investigated: the deterministic rubric, nothing more."""
    from fw import rubric
    n = prof["notification"]
    site = geo.from_geojson(skogs.get_notification(b)["geometry"])
    h = rubric.hint(prof)
    hint = {**h, "rule": re.sub(r"(\d)\.0\b", r"\1", h["rule"])}
    wide = prof["rings"][1000]["redlisted"].values()
    sp_ids = {s["evidence_id"] for s in sorted(wide, key=lambda s: (not tools.rubric._eligible(s), CAT_ORDER.get(s["category"], 9),
                                                                     s["min_distance_m"]))[:12]
              if s["category"] in CAT_ORDER}
    rows = gbif.records_within(site, 1000)["rows"]
    coords = {r["key"]: (r["decimalLongitude"], r["decimalLatitude"]) for r, _ in rows}
    pts = []
    for sp in wide:
        if sp["category"] not in CAT_ORDER:
            continue
        for rec in sp["recs"]:
            if rec["key"] in coords:
                lon, lat = coords[rec["key"]]
                pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                            "properties": {"key": rec["key"], "name": sp["scientific_name"], "swedish": sp["swedish_name"],
                                           "category": sp["category"], "eligible": tools.rubric._eligible(sp),
                                           "mobility": sp["mobility"], "unc_m": rec["unc_m"], "year": rec["year"],
                                           "dist_m": rec["dist_m"],
                                           "evidence_id": sp.get("evidence_id")}})
    eff = prof["effort"]
    ns = eff["near_site"]
    rings = [{"m": 0, "geometry": _gj(site)}] + [{"m": r, "geometry": _gj(geo.buffer_4326(site, r, 5), 0.0001)} for r in RINGS[1:]]
    return {
        "kind": "rule", "beteckn": b, "priority": h["priority"], "rubric_hint": hint, "override_reason": None,
        "forced": None, "notification": {k: n[k] for k in ("kommun", "lan", "avverktyp", "skogstyp", "inkomdatum",
                                                            "anmald_ha", "polygon_ha", "status", "centroid")},
        "window_closes": window_close(n["inkomdatum"]),
        "reasons": [], "contradictions": [], "uncertainties": [
            f"Recording effort: {eff['records']} records within 1000 m ({ref_word(lannr)} {eff['county_median']:g}); "
            f"{ns['within_250m']['records']} within 250 m (median {ns['county_median_within_250m']:g}).",
            f"Effort reference: {ref_basis(lannr, eff)}."],
        "not_established": RULE_NOT_ESTABLISHED,
        "next_action": "Run the agent on this site for a full dossier before deciding on a field check.",
        "counts": tools._rank_counts(prof), "rubric_status": RUBRIC_STATUS.get(lannr, "untested"),
        "evidence": evidence_index(set(h["evidence_ids"]) | sp_ids), "path": [], "reason_kinds": [],
        "species_lines": species_lines(sp_ids, prof), "context": None,
        "geometry": {"site": _gj(site), "rings": rings, "species": {"type": "FeatureCollection", "features": cap_points(pts)},
                     "fellings": {"type": "FeatureCollection", "features": []}},
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
    """Outline for the no-tiles fallback, from OpenStreetMap Nominatim (cached to disk)."""
    name = {"20": "Dalarnas län", "SE": "Sverige"}.get(lannr)
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
        (WEB / "sites" / f"{b.replace(' ', '_')}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
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
    (WEB / "week.json").write_text(json.dumps(week, ensure_ascii=False, separators=(",", ":")))
    (WEB / "replay.json").write_text(json.dumps({"steps": replay, "sites": len(sites)}, ensure_ascii=False))
    (WEB / "evidence_test.json").write_text(json.dumps(evidence_test(), ensure_ascii=False, indent=1))
    shares = [s for s in (zero_share(l) for l in ("21", "17")) if s]
    ws = {"county": COUNTY_NAMES[a.lannr], "received_from": week["received_from"], "received_to": week["received_to"],
          **week_share(sites)}
    (WEB / "stats.json").write_text(json.dumps({
        "week": ws, "zero_record_share": shares,
        "headline": (f"{ws['no_record_within_250m']} of the {ws['n']} sites notified in {ws['county']} this week "
                     f"({round(100 * ws['share_no_record_within_250m'])}%) have no species record within 250 m since 2016.")},
        ensure_ascii=False, indent=1))
    cond = config.OUT / "condense.json"
    (WEB / "condense.json").write_text(cond.read_text() if cond.exists() else json.dumps({"status": "pending"}))
    outline = county_outline(a.lannr)
    (WEB / "county.json").write_text(json.dumps(outline or {}, ensure_ascii=False))
    print(f"web/data: {len(sites)} sites, {len(replay)} replay steps, counts {dict(counts)}")


def main_national() -> None:
    """All of Sweden this week: agent dossiers where an agent ran, rule-only profiles everywhere else."""
    load_photos()
    (WEB / "sites").mkdir(parents=True, exist_ok=True)
    listing = json.loads((config.OUT / "national_week.json").read_text())
    ids = [x["beteckn"] for x in listing]
    lan = {x["beteckn"]: x["lannr"] for x in listing}
    dossiers = {d["beteckn"]: d for d in rank.load_dossiers(ids)}
    by_site = defaultdict(list)
    for e in log_events():
        if e.get("type") == "tool_call":
            d = dossiers.get(e.get("notification"))
            if d and e["run_id"] == d.get("run_id"):
                by_site[e["notification"]].append(e)
    sites, replay, keyed = [], [], []
    for i, b in enumerate(ids, 1):
        prof = tools._profile(b)
        if b in dossiers:
            d = dossiers[b]
            payload = site_payload(b, d, prof, by_site[b])
            payload["rubric_status"] = RUBRIC_STATUS.get(lan[b], "untested")
            head = headline(d)
            for e in by_site[b]:
                replay.append({"ts": e["ts"], "run_id": e["run_id"], "beteckn": b, "tool": e["tool"],
                               "error": e.get("error", False), "text": feed_text(e, prof, d),
                               "final": e["tool"] == "record_finding" and not e.get("error")})
        else:
            payload = rule_payload(b, prof, lan[b])
            head = payload["rubric_hint"]["rule"][0].upper() + payload["rubric_hint"]["rule"][1:]
        (WEB / "sites" / f"{b.replace(' ', '_')}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        n = payload["notification"]
        keyed.append((rank.key({"priority": payload["priority"], "counts": payload["counts"]}), b))
        sites.append({"beteckn": b, "lannr": lan[b], "county": COUNTY_NAMES.get(lan[b], lan[b]), "kommun": n["kommun"],
                      "area_ha": n["polygon_ha"], "received": n["inkomdatum"], "window_closes": payload["window_closes"],
                      "priority": payload["priority"], "rubric_hint": payload["rubric_hint"]["priority"], "kind": payload["kind"],
                      "overridden": payload["priority"] != payload["rubric_hint"]["priority"], "headline": head,
                      "centroid": n["centroid"], "polygon": _gj(geo.from_geojson(payload["geometry"]["site"]), 0.0001)})
        if i % 100 == 0:
            print(f"built {i}/{len(ids)}", flush=True)
    order = {b: r for r, (_, b) in enumerate(sorted(keyed), 1)}
    for x in sites:
        x["rank"] = order[x["beteckn"]]
    sites.sort(key=lambda x: x["rank"])
    # parallel agent processes interleave in time: replay one site at a time, in order of each site's first step
    first = {}
    for r in replay:
        first[r["beteckn"]] = min(first.get(r["beteckn"], r["ts"]), r["ts"])
    replay.sort(key=lambda r: (first[r["beteckn"]], r["beteckn"], r["ts"]))
    counts = Counter(x["priority"] for x in sites)
    counties = []
    for l in sorted({x["lannr"] for x in sites}, key=lambda l: -sum(x["lannr"] == l for x in sites)):
        cs = [x for x in sites if x["lannr"] == l]
        ref = tools._profile(cs[0]["beteckn"])["effort"]
        counties.append({"lannr": l, "county": COUNTY_NAMES.get(l, l), "notified": len(cs),
                         "counts": dict(Counter(x["priority"] for x in cs)), "agent": sum(x["kind"] == "agent" for x in cs),
                         "rubric_status": RUBRIC_STATUS.get(l, "untested"), "effort_median": ref["county_median"],
                         "effort_reference": ref["reference"]})
    week = {"scope": "Sweden", "county": "Sweden", "received_from": "2026-09-28", "received_to": "2026-10-03",
            "notified": len(ids), "investigated": sum(x["kind"] == "agent" for x in sites), "counts": dict(counts),
            "agent_dossiers": sum(x["kind"] == "agent" for x in sites), "overrides": sum(x["overridden"] for x in sites),
            "window_days": WINDOW_DAYS, "counties": counties,
            "rubric_note": "Rubric tuned on Dalarna, tested on Gävleborg and Värmland; elsewhere applied untested.",
            "sites": sites}
    (WEB / "week.json").write_text(json.dumps(week, ensure_ascii=False, separators=(",", ":")))
    (WEB / "replay.json").write_text(json.dumps({"steps": replay, "sites": len({r["beteckn"] for r in replay})}, ensure_ascii=False))
    (WEB / "evidence_test.json").write_text(json.dumps(evidence_test(), ensure_ascii=False, indent=1))
    none250 = sum(tools._profile(b)["rings"][250]["records"] == 0 for b in ids)
    shares = [z for z in (zero_share(l) for l in ("21", "17")) if z]
    (WEB / "stats.json").write_text(json.dumps({
        "week": {"county": "Sweden", "received_from": "2026-09-28", "received_to": "2026-10-03", "n": len(ids),
                 "no_record_within_250m": none250, "share_no_record_within_250m": round(none250 / len(ids), 4)},
        "zero_record_share": shares,
        "headline": (f"{none250} of the {len(ids)} sites notified for regeneration felling in Sweden this week "
                     f"({round(100 * none250 / len(ids))}%) have no species record within 250 m since 2016.")},
        ensure_ascii=False, indent=1))
    cond = config.OUT / "condense.json"
    (WEB / "condense.json").write_text(cond.read_text() if cond.exists() else json.dumps({"status": "pending"}))
    (WEB / "county.json").write_text(json.dumps(county_outline("SE") or {}, ensure_ascii=False))
    print(f"web/data (Sweden): {len(sites)} sites, {week['agent_dossiers']} agent dossiers, {len(replay)} replay steps, "
          f"counts {dict(counts)}")


if __name__ == "__main__":
    main_national() if "--national" in sys.argv else main()
