"""Artportalen species observations via GBIF: record fetches and effort counts."""
from fw import config, geo
from fw.cache import full_url, get_json

PAGE = 300
MAX_RECORDS = 6000  # per site query; above this the profile is flagged as truncated

KEEP = ("key", "species", "scientificName", "speciesKey", "taxonID", "eventDate", "year",
        "coordinateUncertaintyInMeters", "basisOfRecord", "decimalLatitude", "decimalLongitude",
        "identificationVerificationStatus", "occurrenceID")


def record_url(key) -> str:
    return f"https://www.gbif.org/occurrence/{key}"


def _base(wkt: str, since_year: int, until_year: int = 2026) -> dict:
    return {"datasetKey": config.ARTPORTALEN_DATASET, "geometry": wkt, "year": f"{since_year},{until_year}",
            "hasCoordinate": "true", "hasGeospatialIssue": "false", "occurrenceStatus": "PRESENT"}


def _slim(body: dict) -> dict:
    return {"count": body.get("count"), "endOfRecords": body.get("endOfRecords"),
            "results": [{k: r.get(k) for k in KEEP} for r in body.get("results", [])]}


def search_url(wkt: str, since_year: int) -> str:
    """Human-checkable GBIF API URL for the evidence trail."""
    return full_url(f"{config.GBIF_API}/occurrence/search", {**_base(wkt, since_year), "limit": 20})


def fetch_records(geom_4326, since_year: int = 2016, max_records: int = MAX_RECORDS, until_year: int = 2026) -> dict:
    """All Artportalen records inside a lon/lat geometry (paged), slimmed to the fields we use."""
    wkt = geo.gbif_wkt(geom_4326)
    records, offset, total = [], 0, 0
    while offset < max_records:
        body = get_json(f"{config.GBIF_API}/occurrence/search",
                        {**_base(wkt, since_year, until_year), "limit": PAGE, "offset": offset},
                        slim=_slim, slim_tag="slim1")
        total = body["count"]
        records += body["results"]
        offset += PAGE
        if body["endOfRecords"] or not body["results"]:
            break
    return {"records": records, "total": total, "truncated": total > len(records),
            "wkt": wkt, "source_url": search_url(wkt, since_year)}


def records_within(site_4326, radius_m: int = 1000, since_year: int = 2016) -> dict:
    """Records whose reported position lies within radius_m of the polygon edge (0 = inside).

    Fetches a slightly larger buffer (a superset), then keeps records by exact
    distance in metres. Rings and observation effort both use this, so they
    always describe the same geometry and filters.
    """
    from shapely.geometry import Point
    site_3006 = geo.to_3006(site_4326)
    fetched = fetch_records(geo.buffer_4326(site_4326, radius_m + 50), since_year)
    rows = []
    for r in fetched["records"]:
        if r.get("decimalLongitude") is None:
            continue
        d = site_3006.distance(geo.to_3006(Point(r["decimalLongitude"], r["decimalLatitude"])))
        if d <= radius_m:
            rows.append((r, d))
    return {"rows": rows, "fetched": fetched["total"], "truncated": fetched["truncated"],
            "source_url": fetched["source_url"]}


def effort_stats(rows, max_uncertainty_m: float = 1000) -> dict:
    """Observation effort from (record, distance) rows: records, distinct species, distinct days.

    Only records whose coordinate uncertainty is known and <= max_uncertainty_m count, i.e. exactly
    the records the 1000 m species ring keeps. Coarse locality records are reported separately.
    """
    ok = [(r, d) for r, d in rows if r.get("coordinateUncertaintyInMeters") is not None
          and r["coordinateUncertaintyInMeters"] <= max_uncertainty_m]
    return {"records": len(ok),
            "species": len({r.get("species") for r, _ in ok if r.get("species")}),
            "days": len({(r.get("eventDate") or "")[:10] for r, _ in ok if r.get("eventDate")}),
            "coarse_records_excluded": len(rows) - len(ok)}


def count(geom_4326, since_year: int = 2016) -> dict:
    """Cheap effort numbers: record count and distinct species (facet), no records fetched."""
    wkt = geo.gbif_wkt(geom_4326)
    body = get_json(f"{config.GBIF_API}/occurrence/search",
                    {**_base(wkt, since_year), "limit": 0, "facet": "speciesKey", "facetLimit": 5000})
    species = sum(len(f["counts"]) for f in body.get("facets", []))
    return {"records": body["count"], "species": species, "source_url": search_url(wkt, since_year)}
