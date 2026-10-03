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


def _base(wkt: str, since_year: int) -> dict:
    return {"datasetKey": config.ARTPORTALEN_DATASET, "geometry": wkt, "year": f"{since_year},2026",
            "hasCoordinate": "true", "hasGeospatialIssue": "false", "occurrenceStatus": "PRESENT"}


def _slim(body: dict) -> dict:
    return {"count": body.get("count"), "endOfRecords": body.get("endOfRecords"),
            "results": [{k: r.get(k) for k in KEEP} for r in body.get("results", [])]}


def search_url(wkt: str, since_year: int) -> str:
    """Human-checkable GBIF API URL for the evidence trail."""
    return full_url(f"{config.GBIF_API}/occurrence/search", {**_base(wkt, since_year), "limit": 20})


def fetch_records(geom_4326, since_year: int = 2016, max_records: int = MAX_RECORDS) -> dict:
    """All Artportalen records inside a lon/lat geometry (paged), slimmed to the fields we use."""
    wkt = geo.gbif_wkt(geom_4326)
    records, offset, total = [], 0, 0
    while offset < max_records:
        body = get_json(f"{config.GBIF_API}/occurrence/search",
                        {**_base(wkt, since_year), "limit": PAGE, "offset": offset},
                        slim=_slim, slim_tag="slim1")
        total = body["count"]
        records += body["results"]
        offset += PAGE
        if body["endOfRecords"] or not body["results"]:
            break
    return {"records": records, "total": total, "truncated": total > len(records),
            "wkt": wkt, "source_url": search_url(wkt, since_year)}


def count(geom_4326, since_year: int = 2016) -> dict:
    """Cheap effort numbers: record count and distinct species (facet), no records fetched."""
    wkt = geo.gbif_wkt(geom_4326)
    body = get_json(f"{config.GBIF_API}/occurrence/search",
                    {**_base(wkt, since_year), "limit": 0, "facet": "speciesKey", "facetLimit": 5000})
    species = sum(len(f["counts"]) for f in body.get("facets", []))
    return {"records": body["count"], "species": species, "source_url": search_url(wkt, since_year)}


def effort_circle(geom_4326, radius_m: int = 1000, since_year: int = 2016) -> dict:
    """Effort in a circle of radius_m around the polygon's centroid (same definition as the county reference)."""
    circle = geo.to_4326(geo.to_3006(geom_4326).centroid.buffer(radius_m, quad_segs=8))
    return {"radius_m": radius_m, **count(circle, since_year)}
