"""Landscape context for a site. Context only: never part of the rubric or the scored ranking.

1. Completed fellings (satellite change detection) within 1 km of the polygon, in hectares, by year range.
2. Red-listed records that lie inside an area felled AFTER the record was made: the habitat where the
   species was recorded may be gone. The record's coordinate uncertainty still applies.
"""
from shapely.geometry import Point

from fw import config, evidence, gbif, geo, skogs
from fw.profile import build as build_profile

RADIUS_M = 1000
SPLIT_YEAR = 2015


def fellings_near(site_4326, radius_m: int = RADIUS_M) -> list[dict]:
    """Completed fellings whose bounding box meets the site's radius_m buffer (cached query)."""
    minx, miny, maxx, maxy = geo.buffer_4326(site_4326, radius_m).bounds
    feats = skogs.query_features(config.COMPLETED_FELLINGS_URL, "1=1", "Beteckn,Avvdatum,Arealha",
                                 geometry=f"{minx},{miny},{maxx},{maxy}", geometryType="esriGeometryEnvelope",
                                 spatialRel="esriSpatialRelIntersects", inSR=4326)
    out = []
    for f in feats:
        if not f.get("geometry"):
            continue
        p = f["properties"]
        out.append({"beteckn": p.get("Beteckn"), "felled": skogs.epoch_ms_to_date(p.get("Avvdatum")),
                    "geom_4326": geo.from_geojson(f["geometry"]),
                    "geom_3006": geo.to_3006(geo.from_geojson(f["geometry"])).buffer(0)})
    return out


def compute(beteckn: str) -> dict:
    n = skogs.get_notification(beteckn)
    site = geo.from_geojson(n["geometry"])
    zone = geo.to_3006(site).buffer(RADIUS_M)
    fells = fellings_near(site)

    since = before = undated = 0.0
    for f in fells:
        a = f["geom_3006"].intersection(zone).area / 10_000
        if a <= 0:
            continue
        if not f["felled"]:
            undated += a
        elif int(f["felled"][:4]) >= SPLIT_YEAR:
            since += a
        else:
            before += a

    # Red-listed records (as the 1000 m ring keeps them) that lie inside a later felling
    prof = build_profile(beteckn)
    rows = {r["key"]: r for r, _ in gbif.records_within(site, RADIUS_M)["rows"]}
    later = []
    for sp in prof["rings"][RADIUS_M]["redlisted"].values():
        if sp["category"] not in ("CR", "EN", "VU", "NT"):
            continue
        for rec in sp["recs"]:
            r = rows.get(rec["key"])
            if not r or not r.get("eventDate"):
                continue
            made = r["eventDate"][:10]
            pt = geo.to_3006(Point(r["decimalLongitude"], r["decimalLatitude"]))
            for f in fells:
                if f["felled"] and f["felled"] > made and f["geom_3006"].contains(pt):
                    later.append({"species": sp["scientific_name"], "swedish": sp["swedish_name"],
                                  "category": sp["category"], "record_date": made, "felled": f["felled"],
                                  "felling": f["beteckn"], "key": rec["key"], "url": gbif.record_url(rec["key"]),
                                  "unc_m": rec["unc_m"], "dist_m": rec["dist_m"],
                                  "evidence_id": sp.get("evidence_id")})
                    break
    later.sort(key=lambda x: ({"CR": 0, "EN": 1, "VU": 2, "NT": 3}[x["category"]], x["dist_m"]))
    return {
        "radius_m": RADIUS_M,
        "felled_ha": {f"since_{SPLIT_YEAR}": round(since, 2), f"before_{SPLIT_YEAR}": round(before, 2),
                      "undated": round(undated, 2)},
        "zone_ha": round(zone.area / 10_000, 1),
        "records_in_later_fellings": later,
        "note": "Context only; not part of the priority rubric. A record inside a later felling means the habitat "
                "where it was recorded may be gone; the record's coordinate uncertainty still applies.",
    }


def context_evidence(beteckn: str) -> tuple[dict, str]:
    """Compute and store the context as evidence; returns (payload, evidence_id)."""
    c = compute(beteckn)
    eid = evidence.add(beteckn, "landscape_context", c, "Skogsstyrelsen completed fellings (MapServer/6) + Artportalen via GBIF")
    return c, eid
