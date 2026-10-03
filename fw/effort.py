"""County observation-effort reference: how many Artportalen records a typical notified site has nearby.

Definition: records since 2016 within 1 km of the centroid, over a fixed random
sample of ~50 regeneration-felling notifications in the county. Cached to
data/effort_ref_<lannr>.json so it is computed once.
"""
import json
import random
import statistics

from fw import config, geo, gbif, skogs

WINDOW = ("2026-06-01", "2026-10-01")  # same window as the validation eval set
SAMPLE = 50
SEED = 42
RADIUS_M = 1000


def _path(lannr: str):
    return config.DATA / f"effort_ref_{lannr}.json"


def county_reference(lannr: str) -> dict:
    p = _path(lannr)
    if p.exists():
        return json.loads(p.read_text())
    notes = skogs.notifications_between(lannr, *WINDOW)
    sample = random.Random(SEED).sample(notes, min(SAMPLE, len(notes)))
    counts = []
    for n in sample:
        e = gbif.effort_circle(geo.from_geojson(n["geometry"]), RADIUS_M)
        counts.append({"beteckn": n["beteckn"], "records": e["records"], "species": e["species"]})
    recs = sorted(c["records"] for c in counts)
    q = statistics.quantiles(recs, n=4)
    ref = {
        "lannr": lannr, "window": WINDOW, "population": len(notes), "sample": len(sample), "seed": SEED,
        "radius_m": RADIUS_M, "since_year": 2016,
        "median_records": statistics.median(recs), "p25_records": q[0], "p75_records": q[2],
        "median_species": statistics.median(c["species"] for c in counts),
        "zero_record_sites": sum(r == 0 for r in recs), "sites": counts,
    }
    p.write_text(json.dumps(ref, indent=1, ensure_ascii=False))
    return ref


def level(records: int, ref: dict) -> str:
    """'adequate' at or above the county median, 'below_median', or 'low' below the 25th percentile."""
    if records >= ref["median_records"]:
        return "adequate"
    if records >= ref["p25_records"]:
        return "below_median"
    return "low"


def percentile(records: int, ref: dict) -> int:
    recs = [s["records"] for s in ref["sites"]]
    return round(100 * sum(r <= records for r in recs) / len(recs))
