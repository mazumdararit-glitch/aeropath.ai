"""Tests for the planning service and forecast profiles."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aeropath.external.osrm import RouteGeometry
from aeropath.external.provider import PointReading
from aeropath.schemas.common import Activity, Coordinate, Sensitivity
from aeropath.services.planning import (
    ClimatologyProfile,
    DataUnavailableError,
    ForecastProfile,
    PlanRequest,
    PlanService,
)

ORIGIN = Coordinate(lat=28.63, lng=77.21)
DEST = Coordinate(lat=28.64, lng=77.22)


class TestForecastProfile:
    def test_empty_profile_reports_empty(self) -> None:
        profile = ForecastProfile({})
        assert profile.is_empty is True
        assert profile.cleanest_hours() == []

    def test_ranks_cleanest_hours(self) -> None:
        profile = ForecastProfile({8: 150.0, 9: 120.0, 10: 90.0, 11: 200.0})
        assert profile.is_empty is False
        cleanest = profile.cleanest_hours(limit=2)
        assert len(cleanest) == 2
        # Cleanest hour is 10 (AQI 90), then 9 (AQI 120)
        assert cleanest[0] == (10, 90.0)
        assert cleanest[1] == (9, 120.0)


class _StubRouter:
    def __init__(self, geometries: list[RouteGeometry] | None = None) -> None:
        self.geometries = geometries or [
            RouteGeometry(
                coordinates=(ORIGIN, DEST),
                distance_m=1000.0,
                duration_s=600.0,
            )
        ]

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        profile: str = "foot",
        alternatives: int = 0,
    ) -> list[RouteGeometry]:
        return self.geometries


class _StubProvider:
    name = "stub_provider"

    def __init__(self, readings: list[PointReading] | None = None) -> None:
        self.readings = readings or []

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        if self.readings:
            return self.readings
        return [
            PointReading(
                coordinate=p,
                concentrations={"PM2.5": 50.0, "PM10": 80.0, "NO2": 30.0},
                source=self.name,
                nearest_station_km=1.0,
            )
            for p in points
        ]

    async def hourly_forecast(
        self, point: Coordinate, *, hours: int = 96
    ) -> dict[int, dict[str, float]]:
        return {
            0: {"PM2.5": 50.0, "PM10": 80.0, "NO2": 30.0},
            1: {"PM2.5": 40.0, "PM10": 60.0, "NO2": 25.0},
            2: {"PM2.5": 30.0, "PM10": 50.0, "NO2": 20.0},
        }

    async def aclose(self) -> None:
        pass


class TestPlanService:
    @pytest.mark.asyncio
    async def test_plan_with_forecast_generates_windows(self) -> None:
        service = PlanService(_StubProvider(), _StubRouter())  # type: ignore[arg-type]
        request = PlanRequest(
            origin=ORIGIN,
            destination=DEST,
            activity=Activity.WALK,
            sensitivity=Sensitivity.NORMAL,
        )
        plan = await service.plan(request, at=datetime(2026, 11, 21, 6, tzinfo=UTC))
        assert len(plan.routes) == 1
        assert len(plan.best_windows) > 0
        assert plan.best_windows[0].basis == "forecast"

    @pytest.mark.asyncio
    async def test_plan_with_explicit_climatology(self) -> None:
        service = PlanService(_StubProvider(), _StubRouter())  # type: ignore[arg-type]
        request = PlanRequest(
            origin=ORIGIN,
            destination=DEST,
            activity=Activity.WALK,
            sensitivity=Sensitivity.NORMAL,
        )
        climatology = ClimatologyProfile({6: [50.0, 55.0], 7: [70.0, 75.0]})
        plan = await service.plan(
            request,
            at=datetime(2026, 11, 21, 6, tzinfo=UTC),
            climatology=climatology,
        )
        assert len(plan.best_windows) > 0
        assert plan.best_windows[0].basis == "climatology"

    @pytest.mark.asyncio
    async def test_raises_data_unavailable_when_no_readings(self) -> None:
        empty_provider = _StubProvider(readings=[])

        async def empty_conc(
            points: list[Coordinate], at: datetime | None = None
        ) -> list[PointReading]:
            return [
                PointReading(
                    coordinate=p,
                    concentrations={},
                    source="stub",
                    nearest_station_km=float("inf"),
                )
                for p in points
            ]

        empty_provider.concentrations_at = empty_conc  # type: ignore[method-assign]

        service = PlanService(empty_provider, _StubRouter())  # type: ignore[arg-type]
        request = PlanRequest(
            origin=ORIGIN,
            destination=DEST,
            activity=Activity.WALK,
            sensitivity=Sensitivity.NORMAL,
        )
        with pytest.raises(DataUnavailableError):
            await service.plan(request)
