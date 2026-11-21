"""OpenStreetMap road context for a set of points.

The street model needs to know what a place *is*, not just where it is: a
monitoring station on a trunk road and one inside a park sit in the same 500 m
provider cell but should not carry the same estimate.

Two lookups per point:

* the class of the nearest road, which drives ``road_factor``
* distance to the nearest green area and water, which drive the proximity
  factors

Distance is measured to the nearest feature's own geometry, not its centre, and
results are cached because OSM queries are the slowest thing in Phase 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry import Point
from shapely.ops import nearest_points

from aeropath.schemas.common import Coordinate, RoadClass

# OSM ``highway`` values that map onto our road factors. Anything else is
# UNKNOWN, which is neutral, rather than guessed.
_OSM_TO_ROAD_CLASS: dict[str, RoadClass] = {
    "motorway": RoadClass.MOTORWAY,
    "motorway_link": RoadClass.MOTORWAY,
    "trunk": RoadClass.TRUNK,
    "trunk_link": RoadClass.TRUNK,
    "primary": RoadClass.PRIMARY,
    "primary_link": RoadClass.PRIMARY,
    "secondary": RoadClass.SECONDARY,
    "secondary_link": RoadClass.SECONDARY,
    "tertiary": RoadClass.TERTIARY,
    "tertiary_link": RoadClass.TERTIARY,
    "residential": RoadClass.RESIDENTIAL,
    "unclassified": RoadClass.RESIDENTIAL,
    "living_street": RoadClass.LIVING_STREET,
    "pedestrian": RoadClass.PEDESTRIAN,
    "service": RoadClass.SERVICE,
    "footway": RoadClass.FOOTWAY,
    "path": RoadClass.PATH,
    "cycleway": RoadClass.CYCLEWAY,
}

# Busier roads dominate the nearest-road lookup, because a station 10 m from a
# trunk road is on the trunk road even if a footway is 3 m away.
_ROAD_PRIORITY: dict[RoadClass, int] = {
    RoadClass.MOTORWAY: 6,
    RoadClass.TRUNK: 6,
    RoadClass.PRIMARY: 5,
    RoadClass.SECONDARY: 4,
    RoadClass.TERTIARY: 3,
    RoadClass.RESIDENTIAL: 2,
    RoadClass.LIVING_STREET: 1,
    RoadClass.PEDESTRIAN: 1,
    RoadClass.SERVICE: 1,
    RoadClass.CYCLEWAY: 1,
    RoadClass.FOOTWAY: 1,
    RoadClass.PATH: 1,
    RoadClass.UNKNOWN: 0,
}


@dataclass(frozen=True, slots=True)
class RoadContext:
    road_class: RoadClass
    road_name: str | None
    green_distance_m: float | None
    water_distance_m: float | None
    road_distance_m: float


def classify_road(highway: str | None) -> RoadClass:
    if highway is None:
        return RoadClass.UNKNOWN
    return _OSM_TO_ROAD_CLASS.get(str(highway), RoadClass.UNKNOWN)


def _project(point: Coordinate, crs: str) -> Point:
    """Project a WGS84 point into a metric CRS."""
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = transformer.transform(point.lng, point.lat)
    return Point(x, y)


def _project_geometry(geometry: object, crs: str) -> object:
    """Project a WGS84 shapely geometry into a metric CRS.

    Projecting only the query point would measure from a UTM coordinate to a
    lon/lat vertex — millions of metres away, and silently so.
    """
    import shapely
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    # interleaved=False hands the transformer separate x and y arrays, which is
    # the signature Transformer.transform expects.
    return shapely.transform(geometry, transformer.transform, interleaved=False)  # type: ignore[arg-type]


def distance_to_nearest(
    point: Coordinate, geometries: Sequence[object], *, crs: str | None = None
) -> float | None:
    """Metres to the nearest of a set of shapely geometries, or ``None``."""
    if not geometries:
        return None
    if crs is None:
        target = Point(point.lng, point.lat)
        projected = geometries
    else:
        target = _project(point, crs)
        projected = [_project_geometry(g, crs) for g in geometries]

    best: float | None = None
    for geometry in projected:
        _, nearest = nearest_points(target, geometry)  # type: ignore[arg-type]
        distance = target.distance(nearest)
        if best is None or distance < best:
            best = distance
    return best


def build_context(
    point: Coordinate,
    *,
    roads: Sequence[tuple[object, str | None]],
    green_areas: Sequence[object],
    water: Sequence[object],
    crs: str | None = None,
) -> RoadContext:
    """Road class and proximity context for one point."""
    if crs is None:
        target = Point(point.lng, point.lat)
        projected_roads = roads
        projected_green = green_areas
        projected_water = water
    else:
        target = _project(point, crs)
        projected_roads = [(_project_geometry(g, crs), hw) for g, hw in roads]
        projected_green = [_project_geometry(g, crs) for g in green_areas]
        projected_water = [_project_geometry(g, crs) for g in water]

    # Rank each road by (distance band, class priority), then take the best.
    # Banding rather than raw distance implements "a station 10 m from a trunk
    # road is on the trunk road, even if a footway is 3 m away": roads within
    # TIE_BAND_M of each other are treated as the same distance, and the busier
    # class wins. Sorting the candidates makes the order total and independent
    # of iteration order.
    TIE_BAND_M = 25.0
    candidates: list[tuple[int, float, RoadClass, str | None]] = []
    chosen_class = RoadClass.UNKNOWN
    chosen_name: str | None = None
    chosen_distance = float("inf")

    for geometry, highway in projected_roads:
        _, nearest = nearest_points(target, geometry)  # type: ignore[arg-type]
        distance = target.distance(nearest)
        road_class = classify_road(highway)
        band = int(distance // TIE_BAND_M)
        candidates.append((band, distance, road_class, highway))

    if candidates:
        candidates.sort(key=lambda c: (c[0], -_ROAD_PRIORITY[c[2]], c[1]))
        _, best_distance, best_class, best_name = candidates[0]
        chosen_class = best_class
        chosen_name = best_name
        chosen_distance = best_distance

    return RoadContext(
        road_class=chosen_class,
        road_name=chosen_name,
        green_distance_m=_nearest_distance(target, projected_green),
        water_distance_m=_nearest_distance(target, projected_water),
        road_distance_m=chosen_distance if chosen_distance != float("inf") else -1.0,
    )


def _nearest_distance(target: Point, geometries: Sequence[object]) -> float | None:
    best: float | None = None
    for geometry in geometries:
        _, nearest = nearest_points(target, geometry)  # type: ignore[arg-type]
        distance = target.distance(nearest)
        if best is None or distance < best:
            best = distance
    return best
