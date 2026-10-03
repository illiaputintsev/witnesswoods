"""Geometry helpers. Measure in metres in SWEREF99 TM (EPSG:3006), talk to APIs in lon/lat (EPSG:4326)."""
from math import pi, sqrt

from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from shapely.ops import transform

_to_3006 = Transformer.from_crs(4326, 3006, always_xy=True).transform
_to_4326 = Transformer.from_crs(3006, 4326, always_xy=True).transform


def to_3006(geom):
    return transform(_to_3006, geom)


def to_4326(geom):
    return transform(_to_4326, geom)


def from_geojson(geometry: dict):
    return shape(geometry)


def area_ha(geom_4326) -> float:
    return to_3006(geom_4326).area / 10_000


def equivalent_radius_m(geom_4326) -> float:
    """Radius of a circle with the same area as the polygon."""
    return sqrt(to_3006(geom_4326).area / pi)


def buffer_4326(geom_4326, metres: float, simplify_m: float = 20):
    """Buffer in metres, simplify in metres, return lon/lat geometry."""
    return to_4326(to_3006(geom_4326).buffer(metres).simplify(simplify_m))


def gbif_wkt(geom_4326) -> str:
    """WKT for GBIF: lon lat order, counter-clockwise rings, single polygon.

    A multipolygon is replaced by its convex hull (a superset); callers filter
    records by exact distance afterwards, so a superset is safe.
    """
    g = geom_4326
    if g.geom_type != "Polygon":
        g = g.convex_hull
    g = orient(g, sign=1.0)
    return g.wkt
