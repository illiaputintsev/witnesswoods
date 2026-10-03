"""Skogsstyrelsen ArcGIS queries: felling notifications and completed fellings.

The key-habitat layer is deliberately NOT queried here. It lives only in
fw/validate.py so it can never reach the agent.
"""
from datetime import datetime, timezone

from shapely.ops import unary_union

from fw import config, geo
from fw.cache import full_url, get_json

REGEN = "Föryngringsavverkning"
NOTIF_FIELDS = "OBJECTID,Lannr,Lan,Kommun,Beteckn,Avverktyp,Skogstyp,Inkomdatum,Anmaldha,Arendestatus,Natforha"
PAGE = 2000


def epoch_ms_to_date(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def query_features(layer_url: str, where: str, out_fields: str = "*", order_by: str | None = None,
                   limit: int | None = None, **extra) -> list[dict]:
    """Paginated GeoJSON query in lon/lat. Returns a list of GeoJSON features."""
    feats, offset = [], 0
    while True:
        count = PAGE if limit is None else min(PAGE, limit - len(feats))
        params = {"where": where, "outFields": out_fields, "returnGeometry": "true",
                  "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": count, **extra}
        if order_by:
            params["orderByFields"] = order_by
        body = get_json(f"{layer_url}/query", params)
        page = body.get("features", [])
        feats += page
        offset += len(page)
        more = body.get("exceededTransferLimit") or body.get("properties", {}).get("exceededTransferLimit")
        if not page or not more or (limit is not None and len(feats) >= limit):
            return feats


def _clean_notification(feat: dict) -> dict:
    p = feat["properties"]
    received = epoch_ms_to_date(p.get("Inkomdatum"))
    return {
        "beteckn": p["Beteckn"], "lannr": p.get("Lannr"), "lan": p.get("Lan"), "kommun": p.get("Kommun"),
        "avverktyp": p.get("Avverktyp"), "skogstyp": p.get("Skogstyp"), "inkomdatum": received,
        "anmald_ha": p.get("Anmaldha"), "status": p.get("Arendestatus"), "natfor_ha": p.get("Natforha"),
        # sanity check from BRIEF 3.1: the Beteckn year must match the received date
        "date_year_ok": bool(received) and p["Beteckn"].endswith(received[:4]),
        "geometry": feat["geometry"],
    }


def latest_notifications(lannr: str, n: int, regen_only: bool = True) -> list[dict]:
    where = f"Lannr='{lannr}'" + (f" AND Avverktyp='{REGEN}'" if regen_only else "")
    feats = query_features(config.NOTIFICATIONS_URL, where, NOTIF_FIELDS, "Inkomdatum DESC", limit=n)
    return [_clean_notification(f) for f in feats if f.get("geometry")]


def notifications_between(lannr: str, start: str, end: str, regen_only: bool = True) -> list[dict]:
    """Notifications received in [start, end) (YYYY-MM-DD)."""
    where = f"Lannr='{lannr}' AND Inkomdatum >= DATE '{start}' AND Inkomdatum < DATE '{end}'"
    if regen_only:
        where += f" AND Avverktyp='{REGEN}'"
    feats = query_features(config.NOTIFICATIONS_URL, where, NOTIF_FIELDS, "OBJECTID ASC")
    return [_clean_notification(f) for f in feats if f.get("geometry")]


def get_notification(beteckn: str) -> dict:
    feats = query_features(config.NOTIFICATIONS_URL, f"Beteckn='{beteckn}'", NOTIF_FIELDS)
    feats = [f for f in feats if f.get("geometry")]
    if not feats:
        raise KeyError(f"notification {beteckn} not found")
    if len(feats) > 1:  # one notification split into several features: merge them
        geom = unary_union([geo.from_geojson(f["geometry"]) for f in feats])
        feats[0]["geometry"] = geom.__geo_interface__
    return _clean_notification(feats[0])


def completed_felling_overlap(geom_4326) -> dict:
    """Completed fellings (satellite change detection) overlapping a polygon.

    Queries by bounding box, then computes the exact overlap in metres locally.
    """
    minx, miny, maxx, maxy = geom_4326.bounds
    envelope = f"{minx},{miny},{maxx},{maxy}"
    params = dict(geometry=envelope, geometryType="esriGeometryEnvelope",
                  spatialRel="esriSpatialRelIntersects", inSR=4326)
    feats = query_features(config.COMPLETED_FELLINGS_URL, "1=1",
                           "Beteckn,Avvdatum,Arealha,Forebild,Efterbild", **params)
    site = geo.to_3006(geom_4326)
    hits, parts = [], []
    for f in feats:
        g = geo.to_3006(geo.from_geojson(f["geometry"])).buffer(0)
        inter = site.intersection(g)
        if inter.area <= 0:
            continue
        parts.append(inter)
        p = f["properties"]
        hits.append({"beteckn": p.get("Beteckn"), "avvdatum": epoch_ms_to_date(p.get("Avvdatum")),
                     "arealha": p.get("Arealha"), "overlap_ha": round(inter.area / 10_000, 3)})
    overlap = unary_union(parts).area if parts else 0.0
    return {
        "overlap_ha": round(overlap / 10_000, 3),
        "overlap_pct": round(100 * overlap / site.area, 1) if site.area else 0.0,
        "fellings": hits,
        "source_url": full_url(f"{config.COMPLETED_FELLINGS_URL}/query", {"where": "1=1", **params, "f": "geojson"}),
    }
