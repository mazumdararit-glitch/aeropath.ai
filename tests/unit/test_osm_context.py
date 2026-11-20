"""Road context: OSM classification and proximity, without the network."""

from __future__ import annotations

import pytest
from shapely.geometry import LineString, Polygon

from aeropath.external.osm_context import (
    RoadContext,
    build_context,
    classify_road,
    distance_to_nearest,
)
from aeropath.schemas.common import Coordinate


class TestRoadClassification:
    @pytest.mark.parametrize(
        ("highway", "expected"),
        [
            ("motorway", "motorway"),
            ("motorway_link", "motorway"),
            ("trunk", "trunk"),
            ("primary", "primary"),
            ("primary_link", "primary"),
            ("secondary", "secondary"),
            ("tertiary", "tertiary"),
            ("residential", "residential"),
            ("unclassified", "residential"),
            ("living_street", "living_street"),
            ("pedestrian", "pedestrian"),
            ("footway", "footway"),
            ("cycleway", "cycleway"),
            ("path", "path"),
            ("service", "service"),
        ],
    )
    def test_known_classes(self, highway: str, expected: str) -> None:
        assert classify_road(highway).value == expected

    def test_unknown_is_neutral(self) -> None:
        """An unmapped highway value must not silently inflate exposure."""
        assert classify_road("escalator").value == "unknown"
        assert classify_road(None).value == "unknown"


class TestDistanceToNearest:
    def test_returns_none_for_empty(self) -> None:
        assert distance_to_nearest(Coordinate(lat=22.55, lng=88.36), []) is None

    def test_metres_not_degrees(self) -> None:
        """Degrees would be catastrophic: a degree of longitude is ~103 km here."""
        # ~0.001 degree of latitude is ~111 m.
        point = Coordinate(lat=22.55, lng=88.36)
        line = LineString([(88.36, 22.551), (88.36, 22.552)])
        distance = distance_to_nearest(point, [line], crs="EPSG:32646")
        assert distance is not None
        assert 50 < distance < 200, f"expected metres, got {distance}"


class TestBuildContext:
    def test_picks_the_busier_road_when_near_equivalent(self) -> None:
        """A station beside a trunk road is on the trunk road, not the footway."""
        point = Coordinate(lat=22.55, lng=88.36)
        # ~10 m and ~21 m east of the point: same 25 m band, so class decides.
        roads = [
            (LineString([(88.3601, 22.5495), (88.3601, 22.5505)]), "footway"),
            (LineString([(88.3602, 22.5495), (88.3602, 22.5505)]), "trunk"),
        ]
        context = build_context(
            point, roads=roads, green_areas=[], water=[], crs="EPSG:32646"
        )
        assert context.road_class.value == "trunk"

    def test_prefers_the_genuinely_nearest_when_far_apart(self) -> None:
        point = Coordinate(lat=22.55, lng=88.36)
        roads = [
            (LineString([(88.3601, 22.5495), (88.3601, 22.5505)]), "footway"),
            (LineString([(88.4000, 22.5500), (88.4000, 22.5600)]), "trunk"),
        ]
        context = build_context(
            point, roads=roads, green_areas=[], water=[], crs="EPSG:32646"
        )
        assert context.road_class.value == "footway"

    def test_empty_context_is_unknown_not_guessed(self) -> None:
        context = build_context(
            Coordinate(lat=22.55, lng=88.36),
            roads=[],
            green_areas=[],
            water=[],
            crs="EPSG:32646",
        )
        assert isinstance(context, RoadContext)
        assert context.road_class.value == "unknown"
        assert context.green_distance_m is None
        assert context.water_distance_m is None

    def test_measures_green_distance_in_metres(self) -> None:
        point = Coordinate(lat=22.55, lng=88.36)
        # A park polygon roughly 300 m to the east.
        park = Polygon(
            [
                (88.3627, 22.5487),
                (88.3645, 22.5487),
                (88.3645, 22.5512),
                (88.3627, 22.5512),
            ]
        )
        context = build_context(
            point, roads=[], green_areas=[park], water=[], crs="EPSG:32646"
        )
        assert context.green_distance_m is not None
        assert 100 < context.green_distance_m < 500
