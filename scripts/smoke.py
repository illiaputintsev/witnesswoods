"""Hit every data source once and print counts. All responses are cached to disk.

Run: .venv/bin/python scripts/smoke.py
"""
import json
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from shapely.geometry import shape
from shapely.geometry.polygon import orient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw import config
from fw.cache import download_file, get_json

DALARNA = "20"


def arcgis_count(layer_url: str, where: str, **extra) -> int:
    params = {"where": where, "returnCountOnly": "true", "f": "json", **extra}
    return get_json(f"{layer_url}/query", params)["count"]


def date_where(field: str, since: date) -> str:
    """Try ArcGIS DATE syntax first, then timestamp syntax (see BRIEF 3.1)."""
    for clause in (f"{field} >= DATE '{since}'", f"{field} >= timestamp '{since} 00:00:00'"):
        try:
            arcgis_count(config.NOTIFICATIONS_URL, f"Lannr='{DALARNA}' AND {clause}")
            return clause
        except RuntimeError:
            continue
    raise RuntimeError("no date syntax accepted")


def esri_polygon(geom) -> str:
    rings = [[[round(x, 6), round(y, 6)] for x, y in geom.exterior.coords]]
    return json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}, separators=(",", ":"))


def main() -> None:
    today = date.today()

    # 1. Dalarna felling notifications in the last 14 days
    since = today - timedelta(days=14)
    clause = date_where("Inkomdatum", since)
    n_notif = arcgis_count(config.NOTIFICATIONS_URL, f"Lannr='{DALARNA}' AND {clause}")
    n_regen = arcgis_count(
        config.NOTIFICATIONS_URL,
        f"Lannr='{DALARNA}' AND {clause} AND Avverktyp='Föryngringsavverkning'",
    )
    print(f"[skogs] Dalarna notifications since {since} (where: {clause}): {n_notif} "
          f"(regeneration felling: {n_regen})")

    # Newest notification, with polygon, for the per-polygon checks.
    # Sanity-check: epoch-ms date (UTC) year should match the Beteckn year.
    fc = get_json(f"{config.NOTIFICATIONS_URL}/query", {
        "where": f"Lannr='{DALARNA}' AND Avverktyp='Föryngringsavverkning'",
        "outFields": "Beteckn,Kommun,Inkomdatum,Anmaldha",
        "orderByFields": "Inkomdatum DESC", "resultRecordCount": 1,
        "returnGeometry": "true", "outSR": 4326, "f": "geojson",
    })
    newest = fc["features"][0]
    props = newest["properties"]
    received = datetime.fromtimestamp(props["Inkomdatum"] / 1000, tz=timezone.utc).date()
    year_ok = props["Beteckn"].endswith(str(received.year))
    poly = shape(newest["geometry"])
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    print(f"[skogs] newest: {props['Beteckn']} {props['Kommun']} received {received} "
          f"{props['Anmaldha']} ha (Beteckn year matches date: {year_ok})")

    # 2. Completed fellings intersecting one polygon. Use a year-old notification
    #    too, since a brand-new one is unlikely to be felled yet.
    old = get_json(f"{config.NOTIFICATIONS_URL}/query", {
        "where": f"Lannr='{DALARNA}' AND Avverktyp='Föryngringsavverkning' AND "
                 f"Inkomdatum < DATE '{today - timedelta(days=365)}'",
        "outFields": "Beteckn", "orderByFields": "Inkomdatum DESC", "resultRecordCount": 1,
        "returnGeometry": "true", "outSR": 4326, "f": "geojson",
    })["features"][0]
    for label, feat in (("newest", newest), ("year-old", old)):
        g = shape(feat["geometry"])
        g = max(g.geoms, key=lambda x: x.area) if g.geom_type == "MultiPolygon" else g
        n = arcgis_count(
            config.COMPLETED_FELLINGS_URL, "1=1",
            geometry=esri_polygon(g.simplify(0.0002)), geometryType="esriGeometryPolygon",
            spatialRel="esriSpatialRelIntersects", inSR=4326,
        )
        print(f"[skogs] completed fellings intersecting {label} {feat['properties']['Beteckn']}: {n}")

    # 3. GBIF Artportalen records inside the newest polygon (2016+)
    wkt = orient(poly.simplify(0.0002), sign=1.0).wkt  # counter-clockwise, lon lat
    gbif = get_json(f"{config.GBIF_API}/occurrence/search", {
        "datasetKey": config.ARTPORTALEN_DATASET, "geometry": wkt, "year": "2016,2026",
        "hasCoordinate": "true", "hasGeospatialIssue": "false", "occurrenceStatus": "PRESENT",
        "limit": 0, "facet": "speciesKey", "facetLimit": 1000,
    })
    n_species = sum(len(f["counts"]) for f in gbif.get("facets", []))
    print(f"[gbif] Artportalen records in {props['Beteckn']} since 2016: {gbif['count']} "
          f"({n_species} distinct species)")
    # Wider check so we know the query works when the polygon itself is empty
    wide = orient(poly.buffer(0.02).simplify(0.001), sign=1.0).wkt
    gw = get_json(f"{config.GBIF_API}/occurrence/search", {
        "datasetKey": config.ARTPORTALEN_DATASET, "geometry": wide, "year": "2016,2026",
        "hasCoordinate": "true", "hasGeospatialIssue": "false", "occurrenceStatus": "PRESENT",
        "limit": 0,
    })
    print(f"[gbif] same site, ~2 km buffer: {gw['count']}")

    # 4. Swedish Red List 2025
    download_file(config.REDLIST_URL, config.REDLIST_ZIP)
    if not config.REDLIST_CSV.exists():
        zipfile.ZipFile(config.REDLIST_ZIP).extractall(config.REDLIST_DIR)
    rl = pd.read_csv(config.REDLIST_CSV, sep=";", encoding="utf-8-sig", dtype=str)
    cats = rl["Kategori"].str.replace(r"[°*]", "", regex=True).value_counts().to_dict()
    print(f"[redlist] rows: {len(rl)}, columns: {rl.shape[1]}, categories: {cats}")

    # 5. Key habitats in Dalarna (evaluation label only, never an agent tool)
    n_kh = arcgis_count(config.KEY_HABITATS_URL, f"LanKod='{DALARNA}'")
    n_kh_pre = arcgis_count(config.KEY_HABITATS_URL, f"LanKod='{DALARNA}' AND Datinv < DATE '2016-01-01'")
    print(f"[skogs] key habitats in Dalarna: {n_kh} (inventoried before 2016: {n_kh_pre})")


if __name__ == "__main__":
    main()
