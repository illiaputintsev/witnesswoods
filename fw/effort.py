"""County observation-effort reference: how many Artportalen records a typical notified site has nearby.

Definition: Artportalen records since 2016 whose reported position lies within
1000 m of the notification polygon (all parts) and whose coordinate uncertainty
is at most 1000 m, i.e. exactly the records the 1000 m species ring keeps, over a fixed random sample of ~50 regeneration-felling
notifications in the county. Cached to data/effort_ref_<lannr>.json.
"""
import json
import random
import statistics

from fw import config, geo, gbif, skogs

WINDOW = ("2026-06-01", "2026-10-01")  # same window as the validation eval set
SAMPLE = 50
SEED = 42
RADIUS_M = 1000
DEFINITION = "polygon+1000m_since2016_unc1000_near250_v4"  # bump when the definition changes; stale caches are recomputed


def _path(lannr: str):
    return config.DATA / f"effort_ref_{lannr}.json"


NEAR_M = 250


def near_site(rows, site_radius_m: float) -> dict:
    """Near-site effort, by the same rules as the species rings: inside the polygon (uncertainty <= the
    site's equivalent radius, capped at 100 m) and within 250 m (uncertainty <= 250 m)."""
    inside = [(r, d) for r, d in rows if d == 0]
    within = [(r, d) for r, d in rows if d <= NEAR_M]
    return {"inside": gbif.effort_stats(inside, min(site_radius_m, 100)),
            "within_250m": gbif.effort_stats(within, NEAR_M)}


def site_effort(site_4326) -> dict:
    """Effort around one site; same geometry and filters as the species rings."""
    near = gbif.records_within(site_4326, RADIUS_M)
    return {"radius_m": RADIUS_M, **gbif.effort_stats(near["rows"]),
            "near_site": near_site(near["rows"], geo.equivalent_radius_m(site_4326)),
            "truncated": near["truncated"], "source_url": near["source_url"]}


def county_reference(lannr: str) -> dict:
    p = _path(lannr)
    if p.exists():
        ref = json.loads(p.read_text())
        if ref.get("definition") == DEFINITION:
            return ref
    notes = skogs.notifications_between(lannr, *WINDOW)
    sample = random.Random(SEED).sample(notes, min(SAMPLE, len(notes)))
    counts = []
    for n in sample:
        e = site_effort(geo.from_geojson(n["geometry"]))
        counts.append({"beteckn": n["beteckn"], "records": e["records"], "species": e["species"],
                       "days": e["days"], "inside": e["near_site"]["inside"]["records"],
                       "within_250m": e["near_site"]["within_250m"]["records"], "truncated": e["truncated"]})
    recs = sorted(c["records"] for c in counts)
    q = statistics.quantiles(recs, n=4)
    ref = {
        "lannr": lannr, "definition": DEFINITION, "window": WINDOW, "population": len(notes), "sample": len(sample), "seed": SEED,
        "radius_m": RADIUS_M, "since_year": 2016,
        "median_records": statistics.median(recs), "p25_records": q[0], "p75_records": q[2],
        "median_species": statistics.median(c["species"] for c in counts),
        "median_days": statistics.median(c["days"] for c in counts),
        "zero_record_sites": sum(r == 0 for r in recs),
        "median_within_250m": statistics.median(c["within_250m"] for c in counts),
        "median_inside": statistics.median(c["inside"] for c in counts),
        "zero_within_250m_sites": sum(c["within_250m"] == 0 for c in counts), "sites": counts,
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
