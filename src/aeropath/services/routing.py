"""Turn a routed geometry into segments the scoring engine can consume.

The engine scores a list of ``SegmentInput`` with a length and a ``base_aqi``.
A route is a polyline, so this module samples along it and attributes each
sample's air to the length of route it represents.

Why sample at all, rather than score the route as one point: a 5 km walk crosses
several grid cells, and the whole product premise is that which cells you cross
matters. Sampling also keeps the door open for a denser provider later — the
segmentiser does not assume one value per route.

Consecutive samples that fall in the same cell are merged, so the engine sees a
handful of segments rather than hundreds, and the length weighting stays exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import cos, radians, sin, sqrt

from aeropath.domain import grid
from aeropath.external.osrm import RouteGeometry
from aeropath.schemas.common import Coordinate, RoadClass

EARTH_RADIUS_M = 6_371_008.8


def _distance_m(a: Coordinate, b: Coordinate) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (a.lat, a.lng, b.lat, b.lng))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * sqrt(h)


@dataclass(frozen=True, slots=True)
class SampledPoint:
    coordinate: Coordinate
    cell: str


def sample_along(
    geometry: RouteGeometry, *, spacing_m: float, resolution: int
) -> list[SampledPoint]:
    """Points spaced roughly ``spacing_m`` apart along the route.

    Interpolation is linear in lat/lng, which is accurate over the metres-scale
    deltas between consecutive polyline vertices and avoids a projection for what
    is ultimately a 50 m sampling grid.
    """
    if spacing_m <= 0:
        raise ValueError("spacing_m must be positive")

    coords = geometry.coordinates
    if len(coords) < 2:
        raise ValueError("geometry needs at least two coordinates")

    samples: list[SampledPoint] = []
    carry = 0.0

    for start, end in pairwise(coords):
        leg = _distance_m(start, end)
        if leg <= 0:
            continue

        travelled = carry
        while travelled <= leg:
            fraction = travelled / leg
            point = Coordinate(
                lat=start.lat + (end.lat - start.lat) * fraction,
                lng=start.lng + (end.lng - start.lng) * fraction,
            )
            samples.append(
                SampledPoint(
                    coordinate=point,
                    cell=grid.cell_for(point, resolution),
                )
            )
            travelled += spacing_m
        carry = travelled - leg

    if not samples:
        # A route shorter than one spacing still has to produce a segment.
        samples.append(
            SampledPoint(
                coordinate=coords[0], cell=grid.cell_for(coords[0], resolution)
            )
        )
    return samples


@dataclass(frozen=True, slots=True)
class RouteSegment:
    """A run of route inside one grid cell, with the length it contributes."""

    segment_id: int
    cell: str
    coordinate: Coordinate
    length_m: float
    road_class: RoadClass = RoadClass.RESIDENTIAL


def infer_road_class_from_steps(step_names: tuple[str, ...]) -> RoadClass:
    """Infer the representative road class from route street names."""
    if not step_names:
        return RoadClass.RESIDENTIAL

    names_lower = " ".join(s.lower() for s in step_names)
    if any(k in names_lower for k in ["expressway", "motorway", "bypass"]):
        return RoadClass.MOTORWAY
    if any(k in names_lower for k in ["highway", "trunk", "grand trunk"]):
        return RoadClass.TRUNK
    if any(k in names_lower for k in ["arterial", "broadway", "ring road", "sarani"]):
        return RoadClass.PRIMARY
    if any(k in names_lower for k in ["road", "marg", "street", "rasta"]):
        return RoadClass.SECONDARY
    if any(k in names_lower for k in ["park", "lake", "garden", "trail", "path", "footway"]):
        return RoadClass.FOOTWAY
    if any(k in names_lower for k in ["promenade", "ghat", "pedestrian", "mall", "chowk"]):
        return RoadClass.PEDESTRIAN
    if any(k in names_lower for k in ["lane", "gali", "colony", "enclave"]):
        return RoadClass.LIVING_STREET
    return RoadClass.RESIDENTIAL


def segments_from_geometry(
    geometry: RouteGeometry,
    *,
    spacing_m: float = 50.0,
    resolution: int = grid.DEFAULT_RESOLUTION,
) -> list[RouteSegment]:
    """Merge consecutive samples in the same cell into weighted segments."""
    samples = sample_along(geometry, spacing_m=spacing_m, resolution=resolution)
    if not samples:
        return []

    # Each sample stands for one spacing of route; the remainder of the route is
    # distributed evenly so the segment lengths still sum to the route distance.
    per_sample = geometry.distance_m / len(samples) if samples else 0.0
    detected_class = infer_road_class_from_steps(geometry.step_names)

    segments: list[RouteSegment] = []
    for sample in samples:
        previous = segments[-1] if segments else None
        if previous is not None and previous.cell == sample.cell:
            segments[-1] = RouteSegment(
                segment_id=previous.segment_id,
                cell=previous.cell,
                coordinate=previous.coordinate,
                length_m=previous.length_m + per_sample,
                road_class=detected_class,
            )
            continue
        segments.append(
            RouteSegment(
                segment_id=len(segments),
                cell=sample.cell,
                coordinate=sample.coordinate,
                length_m=per_sample,
                road_class=detected_class,
            )
        )
    return segments


def activity_profile(activity: str) -> str:
    """OSRM profile for an activity.

    Cycling is not a supported product activity yet, so both map to walking. Kept
    as a function so adding ``run``-specific routing later is one edit.
    """
    del activity
    return "foot"


# The engine wants a road class per segment. Without the street model this is
# only carried for the response's benefit; road class is a measured null result
# (docs/phase0-results.md, finding 4) and must not drive any number.
NEUTRAL_ROAD_CLASS = RoadClass.UNKNOWN
