"""OSRM routing and geometry segmentisation."""

from __future__ import annotations

import httpx
import pytest
import respx

from aeropath.external.osrm import OsrmRouter, RouteGeometry, RoutingError
from aeropath.schemas.common import Coordinate
from aeropath.services import routing

OSRM = "https://router.project-osrm.org"


def _route_payload(coords: list[list[float]], *, distance: float = 1000.0) -> dict[str, object]:
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": distance,
                "duration": distance / 1.4,
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        ],
    }


class TestOsrmRouter:
    @respx.mock
    async def test_parses_geojson_lon_lat_ordering(self) -> None:
        """GeoJSON is [lon, lat]; Coordinate is lat/lng. Swapping gives a wrong hemisphere."""
        respx.get(url__startswith=f"{OSRM}/route/v1/foot/").mock(
            return_value=httpx.Response(
                200, json=_route_payload([[77.2090, 28.6139], [77.2300, 28.6300]])
            )
        )
        async with OsrmRouter() as router:
            routes = await router.route(
                Coordinate(lat=28.6139, lng=77.2090),
                Coordinate(lat=28.6300, lng=77.2300),
            )
        assert routes[0].coordinates[0].lat == pytest.approx(28.6139)
        assert routes[0].coordinates[0].lng == pytest.approx(77.2090)

    @respx.mock
    async def test_requests_geojson_and_full_overview(self) -> None:
        route = respx.get(url__startswith=f"{OSRM}/route/v1/foot/").mock(
            return_value=httpx.Response(200, json=_route_payload([[1, 2], [3, 4]]))
        )
        async with OsrmRouter() as router:
            await router.route(Coordinate(lat=2, lng=1), Coordinate(lat=4, lng=3))
        params = route.calls[0].request.url.params
        assert params["geometries"] == "geojson"
        assert params["overview"] == "full"

    @respx.mock
    async def test_sends_longitude_before_latitude(self) -> None:
        """OSRM takes lon,lat. Reversing it routes to the wrong continent."""
        route = respx.get(url__startswith=f"{OSRM}/route/v1/foot/").mock(
            return_value=httpx.Response(200, json=_route_payload([[1, 2], [3, 4]]))
        )
        async with OsrmRouter() as router:
            await router.route(
                Coordinate(lat=28.6139, lng=77.2090),
                Coordinate(lat=28.63, lng=77.23),
            )
        path = route.calls[0].request.url.path
        assert "77.209,28.6139" in path

    @respx.mock
    async def test_rate_limit_is_a_clear_error(self) -> None:
        respx.get(url__startswith=f"{OSRM}/route/").mock(return_value=httpx.Response(429))
        async with OsrmRouter() as router:
            with pytest.raises(RoutingError, match="rate limiting"):
                await router.route(Coordinate(lat=1, lng=1), Coordinate(lat=2, lng=2))

    @respx.mock
    async def test_non_ok_code_raises(self) -> None:
        respx.get(url__startswith=f"{OSRM}/route/").mock(
            return_value=httpx.Response(200, json={"code": "NoRoute", "routes": []})
        )
        async with OsrmRouter() as router:
            with pytest.raises(RoutingError, match="NoRoute"):
                await router.route(Coordinate(lat=1, lng=1), Coordinate(lat=2, lng=2))

    async def test_rejects_unknown_profile(self) -> None:
        async with OsrmRouter() as router:
            with pytest.raises(ValueError, match="profile"):
                await router.route(
                    Coordinate(lat=1, lng=1), Coordinate(lat=2, lng=2), profile="hovercraft"
                )


class TestSampling:
    def test_samples_are_spaced_along_the_route(self) -> None:
        # ~1.1 km of latitude; sampling every 100 m should give roughly 11 points.
        geometry = RouteGeometry(
            coordinates=(Coordinate(lat=28.60, lng=77.20), Coordinate(lat=28.61, lng=77.20)),
            distance_m=1110.0,
            duration_s=800.0,
        )
        samples = routing.sample_along(geometry, spacing_m=100.0, resolution=8)
        assert 10 <= len(samples) <= 13

    def test_short_route_still_yields_a_sample(self) -> None:
        geometry = RouteGeometry(
            coordinates=(Coordinate(lat=28.60, lng=77.20), Coordinate(lat=28.6001, lng=77.20)),
            distance_m=11.0,
            duration_s=8.0,
        )
        samples = routing.sample_along(geometry, spacing_m=50.0, resolution=8)
        assert len(samples) == 1

    def test_rejects_non_positive_spacing(self) -> None:
        geometry = RouteGeometry(
            coordinates=(Coordinate(lat=0, lng=0), Coordinate(lat=1, lng=1)),
            distance_m=100.0,
            duration_s=70.0,
        )
        with pytest.raises(ValueError, match="spacing_m"):
            routing.sample_along(geometry, spacing_m=0.0, resolution=8)


class TestSegments:
    def test_segment_lengths_sum_to_route_distance(self) -> None:
        geometry = RouteGeometry(
            coordinates=(Coordinate(lat=28.60, lng=77.20), Coordinate(lat=28.61, lng=77.20)),
            distance_m=1110.0,
            duration_s=800.0,
        )
        segments = routing.segments_from_geometry(geometry, spacing_m=100.0)
        total = sum(segment.length_m for segment in segments)
        assert total == pytest.approx(1110.0, rel=1e-6)

    def test_consecutive_samples_in_one_cell_merge(self) -> None:
        """A short route inside one H3 cell must not produce hundreds of segments."""
        geometry = RouteGeometry(
            coordinates=(
                Coordinate(lat=28.6000, lng=77.2000),
                Coordinate(lat=28.6002, lng=77.2000),
            ),
            distance_m=22.0,
            duration_s=16.0,
        )
        segments = routing.segments_from_geometry(geometry, spacing_m=20.0)
        assert len(segments) < 3

    def test_segment_ids_are_unique_and_ordered(self) -> None:
        geometry = RouteGeometry(
            coordinates=(Coordinate(lat=28.50, lng=77.10), Coordinate(lat=28.65, lng=77.30)),
            distance_m=24000.0,
            duration_s=17000.0,
        )
        segments = routing.segments_from_geometry(geometry, spacing_m=200.0)
        assert [s.segment_id for s in segments] == list(range(len(segments)))
