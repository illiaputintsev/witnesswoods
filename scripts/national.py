"""National week: evidence profiles for every regeneration-felling notification in Sweden (no LLM).

  .venv/bin/python scripts/national.py fetch      freeze the list, fetch data, write county week references
  .venv/bin/python scripts/national.py profiles   build evidence profiles (rubric hints) for every site
  .venv/bin/python scripts/national.py rank       top sites per county by the deterministic rubric

The rubric was tuned on Dalarna and tested on Gävleborg and Värmland; elsewhere it is applied untested.
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw import config, effort, geo, rank, rubric, skogs, tools  # noqa: E402

START, END = "2026-09-28", "2026-10-04"
LIST = config.OUT / "national_week.json"
MIN_SITES = 10
KEEP_REF = {"17", "20", "21"}  # validated references stay as they are


def frozen() -> list[dict]:
    if LIST.exists():
        return json.loads(LIST.read_text())
    where = f"Avverktyp='{skogs.REGEN}' AND Inkomdatum >= DATE '{START}' AND Inkomdatum < DATE '{END}'"
    feats = skogs.query_features(config.NOTIFICATIONS_URL, where, skogs.NOTIF_FIELDS, "OBJECTID ASC")
    sites = [{"beteckn": f["properties"]["Beteckn"], "lannr": f["properties"]["Lannr"], "lan": f["properties"]["Lan"]}
             for f in feats if f.get("geometry")]
    LIST.write_text(json.dumps(sites, ensure_ascii=False))
    return sites


def fetch() -> None:
    sites = frozen()
    print(f"{len(sites)} notifications {START}..{END} (exclusive end)", flush=True)
    stats = {}
    for i, s in enumerate(sites, 1):
        n = skogs.get_notification(s["beteckn"])
        site = geo.from_geojson(n["geometry"])
        skogs.completed_felling_overlap(site)
        e = effort.site_effort(site)
        stats[s["beteckn"]] = {"lannr": s["lannr"], "records": e["records"], "species": e["species"], "days": e["days"],
                               "inside": e["near_site"]["inside"]["records"],
                               "within_250m": e["near_site"]["within_250m"]["records"]}
        if i % 50 == 0:
            print(f"fetched {i}/{len(sites)}", flush=True)
    (config.OUT / "national_effort.json").write_text(json.dumps(stats))
    by = defaultdict(list)
    for b, st in stats.items():
        by[st["lannr"]].append({"beteckn": b, **st})
    national = [{"beteckn": b, **st} for b, st in stats.items()]

    def ref(lannr, rows, basis):
        recs = sorted(r["records"] for r in rows)
        q = statistics.quantiles(recs, n=4) if len(recs) >= 2 else [recs[0]] * 3
        return {"lannr": lannr, "definition": effort.DEFINITION, "basis": basis, "window": (START, END),
                "population": len(rows), "sample": len(rows), "seed": None, "radius_m": effort.RADIUS_M,
                "since_year": 2016, "median_records": statistics.median(recs), "p25_records": q[0],
                "p75_records": q[2], "median_species": statistics.median(r["species"] for r in rows),
                "median_days": statistics.median(r["days"] for r in rows),
                "median_within_250m": statistics.median(r["within_250m"] for r in rows),
                "median_inside": statistics.median(r["inside"] for r in rows),
                "zero_record_sites": sum(r == 0 for r in recs), "sites": [{"beteckn": r["beteckn"], "records": r["records"]} for r in rows]}

    for lannr, rows in by.items():
        if lannr in KEEP_REF:
            continue
        if len(rows) >= MIN_SITES:
            r = ref(lannr, rows, f"this county's own {len(rows)} notifications, week {START}..{END}")
        else:
            r = ref(lannr, national, f"national week median: this county had only {len(rows)} notifications")
        effort.week_path(lannr).write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print("county week references written", flush=True)


def profiles() -> None:
    sites = frozen()
    for arg in sys.argv:  # --part=i/n: build every n-th site starting at i, so n processes can share the work
        if arg.startswith("--part="):
            i, n = map(int, arg.split("=")[1].split("/"))
            sites = sites[i - 1::n]
    for i, s in enumerate(sites, 1):
        tools._profile(s["beteckn"])
        if i % 100 == 0:
            print(f"profiles {i}/{len(sites)}", flush=True)
    print(f"profiles done: {len(sites)}", flush=True)


def ranked_by_county(k: int = 5) -> dict:
    by = defaultdict(list)
    for s in frozen():
        p = tools._profile(s["beteckn"])
        h = rubric.hint(p)
        by[s["lannr"]].append((rank.key({"priority": h["priority"], "counts": tools._rank_counts(p)}), s["beteckn"]))
    return {l: [b for _, b in sorted(v)[:k]] for l, v in by.items()}


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("fetch", "all"):
        fetch()
    if stage in ("profiles", "all"):
        profiles()
    if stage == "rank":
        print(json.dumps(ranked_by_county(), indent=1))
