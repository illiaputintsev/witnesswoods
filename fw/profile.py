"""Evidence profile for one notification, no LLM: felling overlap, effort, and species by distance ring.

Ring r = "within r metres of the polygon edge" (cumulative; 0 = inside).
A record supports a ring only if its coordinate uncertainty is no larger than
the ring's radius. For ring 0 the allowance is the site's equivalent radius
(radius of a circle with the site's area), since nothing has uncertainty 0.
Records with no stated uncertainty never support a ring claim.
"""
from collections import defaultdict

from shapely.geometry import Point

from fw import effort, evidence, gbif, geo, redlist, skogs

RINGS = (0, 100, 250, 500, 1000)
SINCE_YEAR = 2016


def _allowance(ring: int, site_radius: float) -> float:
    return ring if ring > 0 else site_radius


def _flags(sp: dict) -> str:
    return "".join([
        "F" if sp["forest_associated"] else "-",
        "X" if sp["felling_relevant"] else "-",
        {"site_bound": "S", "mobile": "M"}.get(sp["mobility"], "o"),
    ])


def build(beteckn: str, lannr_ref: str | None = None) -> dict:
    n = skogs.get_notification(beteckn)
    site = geo.from_geojson(n["geometry"])
    site_3006 = geo.to_3006(site)
    site_radius = geo.equivalent_radius_m(site)
    centroid = site.centroid

    notif_payload = {k: v for k, v in n.items() if k != "geometry"}
    notif_payload.update(polygon_ha=round(geo.area_ha(site), 2), centroid=[round(centroid.x, 5), round(centroid.y, 5)])
    e_notif = evidence.add(beteckn, "notification", notif_payload, "Skogsstyrelsen felling notifications (MapServer/5)")

    fell = skogs.completed_felling_overlap(site)
    e_fell = evidence.add(beteckn, "completed_felling", {k: v for k, v in fell.items() if k != "source_url"},
                          fell["source_url"])

    ref = effort.county_reference(lannr_ref or n["lannr"])
    eff = gbif.effort_circle(site, effort.RADIUS_M, SINCE_YEAR)
    eff.update(county_median=ref["median_records"], county_p25=ref["p25_records"],
               percentile=effort.percentile(eff["records"], ref), level=effort.level(eff["records"], ref),
               reference=f"{ref['sample']} {ref['lannr']} notifications, {ref['window'][0]}..{ref['window'][1]}")
    e_eff = evidence.add(beteckn, "observation_effort", {k: v for k, v in eff.items() if k != "source_url"},
                         eff["source_url"])

    # Fetch every record within ~1 km of the polygon once, then sort into rings locally
    fetched = gbif.fetch_records(geo.buffer_4326(site, max(RINGS) + 50))
    join = defaultdict(int)
    rows = []
    for r in fetched["records"]:
        if r.get("decimalLongitude") is None:
            continue
        d = site_3006.distance(geo.to_3006(Point(r["decimalLongitude"], r["decimalLatitude"])))
        if d > max(RINGS):
            continue
        row, how = redlist.match(r.get("taxonID"), r.get("species"))
        join["records"] += 1
        join["with_dyntaxa_id"] += redlist.dyntaxa_id(r.get("taxonID")) is not None
        if how:
            join[f"redlisted_by_{how}"] += 1
            if how == "name":
                join.setdefault("name_fallbacks", set()).add(r.get("species"))
        rows.append((r, d, row))

    rings = {}
    species_ev = {}
    for ring in RINGS:
        allow = _allowance(ring, site_radius)
        in_ring = [(r, d, row) for r, d, row in rows if d <= ring]
        unc = [r.get("coordinateUncertaintyInMeters") for r, _, _ in in_ring]
        kept = [(r, d, row) for (r, d, row), u in zip(in_ring, unc) if u is not None and u <= allow]
        red = {}
        for r, d, row in kept:
            if row is None:
                continue
            tid = int(row["taxon_id"])
            sp = red.setdefault(tid, {**redlist.species_summary(row), "records": 0, "latest_year": 0,
                                      "min_uncertainty_m": None, "min_distance_m": None, "record_keys": []})
            sp["records"] += 1
            sp["latest_year"] = max(sp["latest_year"], r.get("year") or 0)
            u = r["coordinateUncertaintyInMeters"]
            sp["min_uncertainty_m"] = u if sp["min_uncertainty_m"] is None else min(sp["min_uncertainty_m"], u)
            sp["min_distance_m"] = round(d) if sp["min_distance_m"] is None else min(sp["min_distance_m"], round(d))
            sp["record_keys"].append(r["key"])
        priority_pool = [s for s in red.values() if s["category"] not in redlist.EXCLUDED]
        thr = [s for s in priority_pool if s["category"] in redlist.THREATENED]
        thr_f = [s for s in thr if s["forest_associated"]]
        thr_fx = [s for s in thr_f if s["felling_relevant"]]
        thr_fxs = [s for s in thr_fx if s["mobility"] == "site_bound"]
        nt_fxs = [s for s in priority_pool if s["category"] == "NT" and s["forest_associated"]
                  and s["felling_relevant"] and s["mobility"] == "site_bound"]
        rings[ring] = {
            "allowance_m": round(allow), "records": len(in_ring), "kept": len(kept),
            "dropped_uncertainty": sum(u is not None and u > allow for u in unc),
            "dropped_no_uncertainty": sum(u is None for u in unc),
            "species": len({r.get("species") for r, _, _ in kept if r.get("species")}),
            "days": len({(r.get("eventDate") or "")[:10] for r, _, _ in kept if r.get("eventDate")}),
            "threatened": len(thr), "threatened_forest": len(thr_f), "threatened_forest_felling": len(thr_fx),
            "eligible": len(thr_fxs), "nt_eligible": len(nt_fxs),
            "mobile_supporting": len([s for s in thr_fx if s["mobility"] == "mobile"]),
            "excluded_dd_re": sorted(f"{s['scientific_name']} ({s['category']})" for s in red.values()
                                     if s["category"] in redlist.EXCLUDED),
            "redlisted": red,
        }
        for tid, sp in red.items():  # keep the widest-ring record set per species as its evidence
            species_ev[tid] = sp

    # One evidence item per red-listed species, carrying the smallest ring it qualifies in
    for tid, sp in species_ev.items():
        first_ring = min(r for r in RINGS if tid in rings[r]["redlisted"])
        payload = {**{k: v for k, v in sp.items() if k != "record_keys"}, "smallest_ring_m": first_ring,
                   "record_urls": [gbif.record_url(k) for k in sp["record_keys"][:10]]}
        eid = evidence.add(beteckn, "redlisted_species", payload, fetched["source_url"])
        for r in RINGS:
            if tid in rings[r]["redlisted"]:
                rings[r]["redlisted"][tid]["evidence_id"] = eid

    join["name_fallbacks"] = sorted(join.get("name_fallbacks", set()))
    return {
        "notification": {**notif_payload, "evidence_id": e_notif},
        "felling": {**fell, "evidence_id": e_fell},
        "effort": {**eff, "evidence_id": e_eff},
        "site_radius_m": round(site_radius), "fetched": fetched["total"], "truncated": fetched["truncated"],
        "join": dict(join), "rings": rings,
    }


def ring_cell(ring: dict) -> str:
    """'T/F/X/S' = threatened / +forest / +felling-relevant / +site-bound species counts."""
    return f"{ring['threatened']}/{ring['threatened_forest']}/{ring['threatened_forest_felling']}/{ring['eligible']}"
