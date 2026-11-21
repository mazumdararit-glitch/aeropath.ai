"""Tests for circular loop generation."""

from __future__ import annotations

import pytest

from aeropath.external.osrm import RouteGeometry
from aeropath.external.provider import PointReading
from aeropath.schemas.common import Activity, Coordinate, Sensitivity
from aeropath.schemas.loop import LoopRequest
from aeropath.services.loop_generator import LoopGenerator


class _StubLoopRouter:
    def __init__(self, distance_m: float = 5000.0) -> None:
        self.distance_m = distance_m

    async def route_waypoints(
        self,
        waypoints: list[Coordinate] | tuple[Coordinate, ...],
        *,
        profile: str = "foot",
        alternatives: int = 0,
    ) -> list[RouteGeometry]:
        return [
            RouteGeometry(
                coordinates=tuple(waypoints),
                distance_m=self.distance_m,
                duration_s=self.distance_m / 1.4,
            )
        ]


class _StubGridProvider:
    name = "stub_grid"

    async def concentrations_at(
        self, points: list[Coordinate], *, at: object = None
    ) -> list[PointReading]:
        return [
            PointReading(
                coordinate=p,
                concentrations={"PM2.5": 40.0, "PM10": 70.0, "NO2": 25.0},
                source=self.name,
                nearest_station_km=0.5,
            )
            for p in points
        ]


@pytest.mark.asyncio
async def test_loop_generator_produces_ranked_loops() -> None:
    router = _StubLoopRouter(distance_m=5000.0)
    provider = _StubGridProvider()
    generator = LoopGenerator(provider=provider, router=router)  # type: ignore[arg-type]

    req = LoopRequest(
        origin=Coordinate(lat=28.63, lng=77.21),
        target_distance_km=5.0,
        activity=Activity.RUN,
        sensitivity=Sensitivity.NORMAL,
        num_alternatives=2,
    )

    loops = await generator.generate_loops(req)
    assert len(loops) > 0
    assert len(loops) <= 2
    # Verify distance is close to 5km
    assert 4000.0 <= loops[0].distance_m <= 6000.0
    assert loops[0].score.mean_aqi > 0
    assert loops[0].is_national_aqi is True
    assert len(loops[0].geometry) > 2
