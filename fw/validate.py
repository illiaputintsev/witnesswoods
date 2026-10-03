"""Held-out evaluation: does the agent's top-k surface known key habitats more often than an
area-matched random selection, without ever seeing them?

  python -m fw.validate --lannr 21 --stage agent      freeze the sample and run the agent on it (blind)
  python -m fw.validate --lannr 21 --stage profiles   build evidence profiles for the whole window (blind, no LLM)
  python -m fw.validate --lannr 21 --stage score      only now fetch key-habitat labels and score
  python -m fw.validate --lannr 20 --hint-only        dev check on Dalarna: rubric-hint ranking only, no LLM

The key-habitat layer (MapServer/3) is queried ONLY in this file and is never registered as an agent
tool. Order is fixed: the sample is frozen, the agent runs blind, and only then are labels fetched.

Label (decided on the dev county before any held-out label was seen): positive = a key habitat
inventoried before 2016 lies within 250 m of the notification polygon (overlap included). Notified
polygons almost never overlap key habitats (1.4% in Dalarna), because owners leave them out.
"""
import argparse
import hashlib
import json
import subprocess
import random
import statistics
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from shapely.ops import unary_union

from fw import config, geo, rank, rubric, skogs, tools

WINDOW_DAYS = 42           # most recent six weeks; the notification layer drops older cases
N, K, SEED, DRAWS = 60, 10, 42, 1000
PRE_DATE = "2016-01-01"    # positives need a key habitat inventoried before this date (temporal separation)
LABEL_M = 250              # positive if such a key habitat lies within this distance of the polygon


def _eval_path(lannr: str) -> Path:
    return config.OUT / f"eval_{lannr}.json"


# ------------------------------------------------------------------ 1. frozen sample

def sample(lannr: str, today: date) -> dict:
    """Freeze the eval sample once. Reruns reuse it, so the held-out set can never be re-drawn."""
    p = _eval_path(lannr)
    if p.exists():
        return json.loads(p.read_text())
    start, end = today - timedelta(days=WINDOW_DAYS), today + timedelta(days=1)
    notes = sorted(skogs.notifications_between(lannr, start.isoformat(), end.isoformat()), key=lambda n: n["beteckn"])
    picked = random.Random(SEED).sample(notes, min(N, len(notes)))
    ev = {"lannr": lannr, "window": [start.isoformat(), end.isoformat()], "population": len(notes),
          "seed": SEED, "n_sampled": len(picked), "sample": [n["beteckn"] for n in picked], "run_id": None,
          "population_ids": [n["beteckn"] for n in notes]}
    config.OUT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ev, indent=1, ensure_ascii=False))
    return ev


# ------------------------------------------------------------------ 2. labels (after the agent run)

def _ms_to_date(ms) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def label(beteckn: str) -> dict:
    """1 = a key habitat inventoried before 2016 within 250 m of the polygon; 0 = no key habitat within 250 m;
    None = excluded (only key habitats inventoried in 2016 or later, or undated, within 250 m)."""
    n = skogs.get_notification(beteckn)
    site = geo.from_geojson(n["geometry"])
    minx, miny, maxx, maxy = geo.buffer_4326(site, LABEL_M + 50).bounds
    feats = skogs.query_features(config.KEY_HABITATS_URL, "1=1", "Beteckn,Datinv,Biotop1,Hektar",
                                 geometry=f"{minx},{miny},{maxx},{maxy}", geometryType="esriGeometryEnvelope",
                                 spatialRel="esriSpatialRelIntersects", inSR=4326)
    s3006 = geo.to_3006(site)
    near = []
    for f in feats:
        if not f.get("geometry"):
            continue
        g = geo.to_3006(geo.from_geojson(f["geometry"])).buffer(0)
        d = s3006.distance(g)
        if d > LABEL_M:
            continue
        near.append({"id": f["properties"].get("Beteckn"), "datinv": _ms_to_date(f["properties"].get("Datinv")),
                     "biotop": f["properties"].get("Biotop1"), "distance_m": round(d, 1),
                     "overlap_ha": round(s3006.intersection(g).area / 10_000, 3)})
    pre = [o for o in near if o["datinv"] and o["datinv"] < PRE_DATE]
    y = 1 if pre else (None if near else 0)
    return {"beteckn": beteckn, "label": y, "key_habitats_within_250m": near,
            "overlaps_pre2016": any(o["overlap_ha"] > 0 for o in pre), "polygon_ha": round(geo.area_ha(site), 2)}


# ------------------------------------------------------------------ 3. metrics

def tiebreak(ids: list[str]) -> dict:
    """Pre-registered seeded order for equal ranking keys (never beteckn order, never in our favour)."""
    return {b: i for i, b in enumerate(random.Random(SEED).sample(sorted(ids), len(ids)))}


def tie_group_at_cutoff(order: list[tuple], k: int) -> int:
    """Size of the group of equal keys that straddles position k (0 if the cut-off falls between keys)."""
    if len(order) <= k or order[k - 1][0] != order[k][0]:
        return 0
    return sum(1 for key, _ in order if key == order[k][0])


def area_bins(eval_ids: list[str], area: dict) -> dict:
    qs = statistics.quantiles([area[b] for b in eval_ids], n=4)
    return {b: sum(area[b] > q for q in qs) for b in eval_ids}


def baseline(top: list[str], eval_ids: list[str], labels: dict, bins: dict, k: int, seed: int) -> list[float]:
    """Each draw picks k notifications from the eval set with the same area-quartile mix as `top`."""
    need = Counter(bins[b] for b in top)
    members = {q: [b for b in eval_ids if bins[b] == q] for q in set(bins.values())}
    rng = random.Random(seed)
    return [sum(labels[b] for q, c in need.items() for b in rng.sample(members[q], c)) / k for _ in range(DRAWS)]


def score(name: str, keyed: list[tuple], eval_ids: list[str], labels: dict, bins: dict, k: int = K) -> dict:
    tb = tiebreak(eval_ids)
    order = sorted(keyed, key=lambda kb: (kb[0], tb[kb[1]]))
    top = [b for _, b in order[:k]]  # one realised selection: precision, area mix and p all refer to it
    hits = sum(labels[b] for b in top)
    prec = hits / k
    draws = baseline(top, eval_ids, labels, bins, k, SEED + 1)
    mean = statistics.mean(draws)
    p95 = round(sorted(draws)[int(0.95 * DRAWS) - 1], 4)
    at_least = sum(round(d * k) >= hits for d in draws)
    return {"ranking": name, "k": k, "n": len(eval_ids), "hits": hits, "precision_at_k": round(prec, 4),
            "tie_group_at_cutoff": tie_group_at_cutoff(order, k),
            "top_k": [{"beteckn": b, "label": labels[b], "bin": bins[b]} for b in top],
            "baseline_mean": round(mean, 4), "baseline_p95": p95,
            "p_share": round(at_least / DRAWS, 4),                 # BRIEF: share of draws at or above
            "p_value": round((at_least + 1) / (DRAWS + 1), 4),     # +1 correction, never exactly 0
            "lift": round(prec / mean, 3) if mean else None,
            "histogram": dict(sorted(Counter(round(d, 2) for d in draws).items()))}


def provenance() -> dict:
    root = config.ROOT
    def sh(*cmd):
        return subprocess.run(cmd, cwd=root, capture_output=True, text=True).stdout.strip()
    files = ["fw/validate.py", "fw/rubric.py", "fw/rank.py", "fw/profile.py", "fw/effort.py", "prompts/rubric.md"]
    return {"scored_at": datetime.now().isoformat(timespec="seconds"), "git_head": sh("git", "rev-parse", "HEAD"),
            "git_dirty": bool(sh("git", "status", "--porcelain", *files)),
            "sha256": {f: hashlib.sha256((root / f).read_bytes()).hexdigest()[:16] for f in files}}


def leakage_check() -> dict:
    fw = Path(__file__).resolve().parent
    users = sorted(p.name for p in fw.glob("*.py")
                   if "KEY_HABITATS_URL" in p.read_text() and p.name not in ("config.py", "validate.py"))
    tool_names = [t["name"] for t in tools.SCHEMAS]
    return {"key_habitat_layer_used_outside_validate": users,
            "label": f"key habitat inventoried before {PRE_DATE} within {LABEL_M} m of the polygon",
            "agent_tools": tool_names,
            "key_habitat_tool_registered": any("habitat" in t or "nyckel" in t for t in tool_names),
            "species_records_since_year": 2016, "positive_requires_datinv_before": PRE_DATE,
            "note": "Strong temporal separation, not perfect independence: pre-2016 inventories may still have "
                    "influenced later recording near the same sites."}


# ------------------------------------------------------------------ main

def hint_keyed(ids: list[str]) -> list[tuple]:
    out = []
    for b in ids:
        p = tools._profile(b)
        out.append((rank.key({"priority": rubric.hint(p)["priority"], "counts": tools._rank_counts(p)}), b))
    return out


def window_ids(ev: dict) -> list[str]:
    """The population frozen at sampling time (the live layer changes during the day)."""
    if "population_ids" not in ev:  # older eval files (dev only)
        ev["population_ids"] = sorted(n["beteckn"] for n in skogs.notifications_between(ev["lannr"], *ev["window"]))
        _eval_path(ev["lannr"]).write_text(json.dumps(ev, indent=1, ensure_ascii=False))
    return ev["population_ids"]


def evaluate(ids: list[str], labs: dict, rankings: dict, ks: tuple) -> dict:
    excluded = [b for b in ids if labs[b]["label"] is None]
    eval_ids = [b for b in ids if labs[b]["label"] is not None]
    labels = {b: labs[b]["label"] for b in eval_ids}
    area = {b: labs[b]["polygon_ha"] for b in eval_ids}
    bins = area_bins(eval_ids, area)
    res = {name: [score(name, [kb for kb in keyed if kb[1] in labels], eval_ids, labels, bins, k) for k in ks]
           for name, keyed in rankings.items()}
    return {"n_total": len(ids), "excluded_post2016_only": len(excluded), "n_eval": len(eval_ids),
            "positives": sum(labels.values()), "base_rate": round(sum(labels.values()) / len(eval_ids), 4),
            "positives_overlapping_polygon": sum(1 for b in eval_ids if labs[b]["overlaps_pre2016"]),
            "area_quartile_edges_ha": [round(q, 2) for q in statistics.quantiles(list(area.values()), n=4)],
            "results": res}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lannr", required=True)
    ap.add_argument("--stage", choices=("agent", "profiles", "score"), default="score")
    ap.add_argument("--hint-only", action="store_true", help="dev check: score the rubric-hint ranking only, no LLM")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--rescore", action="store_true", help="write a new timestamped file; never overwrite the first score")
    a = ap.parse_args()
    if a.hint_only and a.stage == "agent":
        ap.error("--stage agent cannot be combined with --hint-only")

    ev = sample(a.lannr, date.fromisoformat(a.today))
    print(f"county {a.lannr}: {ev['population']} regeneration notifications received {ev['window'][0]}.."
          f"{ev['window'][1]} (exclusive end); agent sample {ev['n_sampled']} with seed {ev['seed']}")
    ids = ev["sample"]

    if a.stage == "agent" and not a.hint_only:
        if ev["run_id"] is not None:
            raise SystemExit(f"agent already ran on this sample (run {ev['run_id']}); the held-out run is never repeated")
        for b in ids:
            tools._profile(b)
        from fw.agent import Run
        run = Run(config.FW_ROUTE, config.FW_MODEL, config.FW_BUDGET_USD, f"eval{a.lannr}")
        print(f"agent run {run.run_id} on {len(ids)} notifications (blind)")
        run.batch(ids)
        ev = json.loads(_eval_path(a.lannr).read_text())
        ev["run_id"], ev["agent_totals"] = run.run_id, run.totals
        _eval_path(a.lannr).write_text(json.dumps(ev, indent=1, ensure_ascii=False))
        return
    if a.stage == "profiles":
        all_ids = window_ids(ev)
        for i, b in enumerate(all_ids, 1):
            tools._profile.__wrapped__(b)  # builds and caches data + evidence; no labels
            if i % 50 == 0:
                print(f"profiles {i}/{len(all_ids)}", flush=True)
        print(f"profiles done: {len(all_ids)}")
        return

    # ---- score: labels are fetched only here, after the blind stages
    if a.lannr != "20" and ev["run_id"] is None:
        raise SystemExit("held-out labels are fetched only after the agent run (run --stage agent first)")
    name = "validation.json" if not a.hint_only else f"validation_hint_{a.lannr}.json"
    if (config.OUT / name).exists() and not a.hint_only:
        if not a.rescore:
            raise SystemExit(f"out/{name} exists: the held-out county is scored once. Use --rescore for a separate file.")
        name = f"validation_rescore_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    dossiers = {}
    if not a.hint_only:
        if ev["run_id"] is None:
            raise SystemExit("run --stage agent first")
        dossiers = {d["beteckn"]: d for d in rank.load_dossiers(ids) if d.get("run_id") == ev["run_id"]}
        missing = sorted(set(ids) - set(dossiers))
        if missing:
            raise SystemExit(f"run {ev['run_id']} has no dossier for {missing}; not scoring a partial run")
    all_ids = window_ids(ev)
    labs = {b: label(b) for b in sorted(set(all_ids) | set(ids))}

    rankings = {"rubric_hint": hint_keyed(ids)}
    if dossiers:
        rankings["agent"] = [(rank.key(dossiers[b]), b) for b in ids]
        # The sample's hint ranking must equal the hint the agent actually saw, stored in each dossier
        stored = {b: dossiers[b]["rubric_hint"]["priority"] for b in ids}
        recomputed = {b: rubric.hint(tools._profile(b))["priority"] for b in ids}
        if stored != recomputed:
            raise SystemExit(f"rubric hint changed since the agent run: {[b for b in ids if stored[b] != recomputed[b]]}")
    sample_eval = evaluate(ids, labs, rankings, (K,))
    n_all = len(all_ids)
    large = evaluate(all_ids, labs, {"rubric_hint": hint_keyed(all_ids)}, (K, max(K, round(0.1 * n_all))))

    out = {
        "lannr": a.lannr, "window": ev["window"], "population": ev["population"], "seed": SEED, "k": K,
        "draws": DRAWS, "run_id": ev.get("run_id"), "agent_totals": ev.get("agent_totals"),
        "agent_sample": sample_eval, "all_notifications_hint_only": large,
        "leakage": leakage_check(), "labels": [labs[b] for b in ids],
        "provenance": provenance(),
    }
    r = sample_eval["results"].get("agent", sample_eval["results"]["rubric_hint"])[0]
    p_txt = "p<0.001" if r["p_share"] == 0 else f"p={r['p_value']}"
    if r["lift"] is None:
        out["statement"] = (f"The {r['ranking']} top-{K} had precision {r['precision_at_k']}; the area-matched random "
                            f"baseline was 0 in all {DRAWS} draws, so lift is undefined (N={sample_eval['n_eval']}, "
                            f"k={K}, {p_txt}).")
    else:
        out["statement"] = (f"Without access to the key-habitat layer, the {r['ranking']} top-{K} were {r['lift']} times "
                            f"as likely as an area-matched random selection to lie within {LABEL_M} m of a key habitat "
                            f"inventoried before 2016 (N={sample_eval['n_eval']}, k={K}, {p_txt}).")
    (config.OUT / name).write_text(json.dumps(out, indent=1, ensure_ascii=False))

    for title, block in (("agent sample", sample_eval), ("all notifications in window, hint only", large)):
        print(f"\n{title}: N={block['n_total']}, excluded (only 2016+/undated key habitats within {LABEL_M} m) "
              f"{block['excluded_post2016_only']}, N_eval={block['n_eval']}, positives={block['positives']} "
              f"(of which overlapping the polygon: {block['positives_overlapping_polygon']}), "
              f"base rate={block['base_rate']}")
        for rows in block["results"].values():
            for rr in rows:
                print(f"  {rr['ranking']:<12} precision@{rr['k']}={rr['precision_at_k']} ({rr['hits']} hits)  "
                      f"baseline mean={rr['baseline_mean']} p95={rr['baseline_p95']}  p={rr['p_value']} "
                      f"(share at/above {rr['p_share']})  lift={rr['lift']}  "
                      f"(tie group at cutoff: {rr['tie_group_at_cutoff']})")
    print("\nleakage:", json.dumps(out["leakage"], ensure_ascii=False))
    print(out["statement"])


if __name__ == "__main__":
    main()
