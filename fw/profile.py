"""Evidence profile for one notification, no LLM: felling overlap, effort, and species by distance ring.

Ring r = "within r metres of the polygon edge" (cumulative; 0 = inside).
A record supports a ring only if its coordinate uncertainty is no larger than
the ring's radius. For ring 0 the allowance is the site's equivalent radius
(radius of a circle with the site's area), since nothing has uncertainty 0,
capped at 100 m so ring 0 stays a subset of ring 100. Records with no stated
uncertainty never support a ring claim.
"""
from collections import defaultdict

from fw import effort, evidence, gbif, geo, redlist, skogs

RINGS = (0, 100, 250, 500, 1000)
SINCE_YEAR = 2016


def _allowance(ring: int, site_radius: float) -> float:
    return ring if ring > 0 else min(site_radius, RINGS[1])


def _rec(r: dict, d: float) -> dict:
    return {"key": r["key"], "dist_m": round(d), "unc_m": r["coordinateUncertaintyInMeters"], "year": r.get("year")}


def ring_summary(buffer_m: int, ring: dict) -> dict:
    """Evidence payload for one ring: counts plus one line per red-listed species (nearest qualifying record)."""
    return {"buffer_m": buffer_m, "since_year": SINCE_YEAR,
            **{k: ring[k] for k in ("allowance_m", "records", "kept", "dropped_uncertainty", "dropped_no_uncertainty",
                                     "species", "days", "threatened", "threatened_forest", "threatened_forest_felling",
                                     "eligible", "nt_eligible", "mobile_supporting", "excluded_dd_re")},
            "redlisted": [{"name": s["scientific_name"], "category": s["category"], "records": s["records"],
                           "latest_year": s["latest_year"], "nearest_record": s["nearest_record"],
                           "evidence_id": s["evidence_id"]}
                          for s in sorted(ring["redlisted"].values(), key=lambda s: (s["scientific_name"]))]}


def _flags(sp: dict) -> str:
    return "".join([
        "F" if sp["forest_associated"] else "-",
        "X" if sp["felling_relevant"] else "-",
        {"site_bound": "S", "mobile": "M"}.get(sp["mobility"], "o"),
    ])


def build(beteckn: str, lannr_ref: str | None = None) -> dict:
    n = skogs.get_notification(beteckn)
    site = geo.from_geojson(n["geometry"])
    site_radius = geo.equivalent_radius_m(site)
    centroid = site.centroid

    notif_payload = {k: v for k, v in n.items() if k != "geometry"}
    notif_payload.update(polygon_ha=round(geo.area_ha(site), 2), centroid=[round(centroid.x, 5), round(centroid.y, 5)])
    e_notif = evidence.add(beteckn, "notification", notif_payload, "Skogsstyrelsen felling notifications (MapServer/5)")

    fell = skogs.completed_felling_overlap(site)
    e_fell = evidence.add(beteckn, "completed_felling", {k: v for k, v in fell.items() if k != "source_url"},
                          fell["source_url"])

    # Fetch every record within 1000 m of the polygon once. Effort and all rings use these same rows.
    near = gbif.records_within(site, max(RINGS), SINCE_YEAR)
    ref = effort.county_reference(lannr_ref or n["lannr"])
    eff = {"radius_m": effort.RADIUS_M, **gbif.effort_stats(near["rows"]), "truncated": near["truncated"]}
    eff.update(county_median=ref["median_records"], county_p25=ref["p25_records"],
               percentile=effort.percentile(eff["records"], ref), level=effort.level(eff["records"], ref),
               definition="records since 2016 within 1000 m of the polygon, coordinate uncertainty <= 1000 m",
               near_site={**effort.near_site(near["rows"], site_radius),
                          "county_median_within_250m": ref["median_within_250m"],
                          "county_median_inside": ref["median_inside"],
                          "note": "Use these near-site figures, not the 1000 m figure, when describing absence of "
                                  "records on or next to the site."},
               reference=f"{ref['sample']} county {ref['lannr']} notifications, {ref['window'][0]}..{ref['window'][1]}")
    e_eff = evidence.add(beteckn, "observation_effort", eff, near["source_url"])
    fetched = {"total": near["fetched"], "truncated": near["truncated"], "source_url": near["source_url"]}

    join = defaultdict(int)
    rows = []
    for r, d in near["rows"]:
        row, how = redlist.match(r.get("taxonID"), r.get("species"))
        join["records"] += 1
        join["with_dyntaxa_id"] += redlist.dyntaxa_id(r.get("taxonID")) is not None
        join["no_uncertainty"] += r.get("coordinateUncertaintyInMeters") is None
        if how:
            join[f"redlisted_by_{how}"] += 1
            if how == "name":
                join.setdefault("name_fallbacks", set()).add(r.get("species"))
        rows.append((r, d, row))

    rings = {}
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
            sp = red.setdefault(tid, {**redlist.species_summary(row), "records": 0, "latest_year": 0, "recs": []})
            sp["records"] += 1
            sp["latest_year"] = max(sp["latest_year"], r.get("year") or 0)
            sp["recs"].append(_rec(r, d))
        for sp in red.values():
            # Each record keeps its own distance, uncertainty and year; nearest first
            sp["recs"].sort(key=lambda x: (x["dist_m"], x["unc_m"], -(x["year"] or 0)))
            sp["nearest_record"] = sp["recs"][0]
            sp["min_distance_m"] = sp["recs"][0]["dist_m"]
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

    # One evidence item per red-listed species. It states its scope explicitly: the nearest
    # qualifying record per ring, and record links ordered from the smallest qualifying ring outwards.
    all_tids = sorted({tid for r in RINGS for tid in rings[r]["redlisted"]})
    for tid in all_tids:
        in_rings = [r for r in RINGS if tid in rings[r]["redlisted"]]
        first = rings[in_rings[0]]["redlisted"][tid]
        widest = rings[in_rings[-1]]["redlisted"][tid]
        ordered = list(first["recs"]) + [x for x in widest["recs"] if x not in first["recs"]]
        payload = {
            **{k: v for k, v in first.items() if k not in ("recs", "records", "latest_year", "nearest_record",
                                                          "min_distance_m")},
            "smallest_ring_m": in_rings[0],
            "nearest_record_by_ring": {str(r): rings[r]["redlisted"][tid]["nearest_record"] for r in in_rings},
            "records_by_ring": {str(r): rings[r]["redlisted"][tid]["records"] for r in in_rings},
            "latest_year_by_ring": {str(r): rings[r]["redlisted"][tid]["latest_year"] for r in in_rings},
            "records": [{**x, "url": gbif.record_url(x["key"])} for x in ordered[:10]],
            "record_urls": [gbif.record_url(x["key"]) for x in ordered[:10]],
        }
        eid = evidence.add(beteckn, "redlisted_species", payload, fetched["source_url"])
        for r in in_rings:
            rings[r]["redlisted"][tid]["evidence_id"] = eid

    # Every ring summary is evidence too, including empty rings ("0 records within 250 m")
    for r in RINGS:
        rings[r]["evidence_id"] = evidence.add(beteckn, "species_ring", ring_summary(r, rings[r]), fetched["source_url"])

    join["name_fallbacks"] = sorted(join.get("name_fallbacks", set()))
    return {
        "notification": {**notif_payload, "evidence_id": e_notif},
        "felling": {**fell, "evidence_id": e_fell},
        "effort": {**eff, "evidence_id": e_eff},
        "site_radius_m": round(site_radius), "fetched": fetched["total"], "truncated": fetched["truncated"],
        "fetch_url": fetched["source_url"],
        "join": dict(join), "rings": rings,
    }


def ring_cell(ring: dict) -> str:
    """'T/F/X/S' = threatened / +forest / +felling-relevant / +site-bound species counts."""
    return f"{ring['threatened']}/{ring['threatened_forest']}/{ring['threatened_forest_felling']}/{ring['eligible']}"
