"""End-to-end integration tests for core AEROPATH API flows.

Tests:
1. /v1/plan: Full routing and dose planning with multiple candidate routes.
2. /v1/airquality: Single-point air quality probe and advisory.
3. /v1/routes/loop: Circular fitness loop generation.
4. Error handling: 502 when router fails, 503 when grid data is missing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from aeropath.api.main import create_app
from aeropath.api.v1.airquality import get_grid_provider
from aeropath.api.v1.routes import get_loop_generator
from aeropath.config import Settings
from aeropath.external.osrm import RouteGeometry, RoutingError
from aeropath.external.provider import PointReading
from aeropath.schemas.common import Activity, Coordinate, Sensitivity
from aeropath.services.loop_generator import LoopGenerator
from aeropath.services.planning import DataUnavailableError, PlanService
from aeropath.services.scoring import street_model


class _RecordedGridProvider:
    name = "recorded_mock_grid"

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        return [
            PointReading(
                coordinate=p,
                concentrations={
                    "PM2.5": 42.0,
                    "PM10": 85.0,
                    "NO2": 32.0,
                    "SO2": 15.0,
                    "CO": 0.8,
                    "O3": 25.0,
                },
                source=self.name,
                nearest_station_km=0.8,
                observed_at=datetime.now(UTC),
            )
            for p in points
        ]


class _RecordedRouter:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        profile: str = "foot",
        alternatives: int = 0,
    ) -> list[RouteGeometry]:
        if self.should_fail:
            raise RoutingError("Mock upstream router connection timed out")

        # Provide a direct route and an alternative route
        direct = RouteGeometry(
            coordinates=(
                origin,
                Coordinate(
                    lat=(origin.lat + destination.lat) / 2,
                    lng=(origin.lng + destination.lng) / 2,
                ),
                destination,
            ),
            distance_m=2000.0,
            duration_s=1500.0,
        )
        detour = RouteGeometry(
            coordinates=(
                origin,
                Coordinate(lat=origin.lat + 0.005, lng=origin.lng + 0.005),
                Coordinate(lat=destination.lat + 0.003, lng=destination.lng + 0.003),
                destination,
            ),
            distance_m=2200.0,
            duration_s=1650.0,
        )
        return [direct, detour] if alternatives > 0 else [direct]

    async def route_waypoints(
        self,
        waypoints: list[Coordinate] | tuple[Coordinate, ...],
        *,
        profile: str = "foot",
        alternatives: int = 0,
    ) -> list[RouteGeometry]:
        if self.should_fail:
            raise RoutingError("Mock upstream waypoint router timed out")

        coords = tuple(waypoints)
        return [
            RouteGeometry(
                coordinates=coords,
                distance_m=3500.0,
                duration_s=2500.0,
            )
        ]


class _FailingGridProvider:
    name = "failing_grid"

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        raise DataUnavailableError("Grid data coverage missing for test region")


@pytest.fixture
def integration_client() -> TestClient:
    settings = Settings(xkdr_api_key="integration-key", grid_provider="recorded_mock")
    grid = _RecordedGridProvider()
    router = _RecordedRouter()
    service = PlanService(grid, router, street_params=street_model.IDENTITY_MODEL)  # type: ignore[arg-type]
    app = create_app(settings, service=service)
    app.dependency_overrides[get_grid_provider] = lambda: grid
    app.dependency_overrides[get_loop_generator] = lambda: LoopGenerator(
        grid,
        router,  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_integration_plan_flow(integration_client: TestClient) -> None:
    """Test full /v1/plan route optimization flow."""
    payload = {
        "origin": {"lat": 28.6139, "lng": 77.2090},
        "destination": {"lat": 28.6250, "lng": 77.2180},
        "activity": Activity.RUN.value,
        "sensitivity": Sensitivity.SENSITIVE.value,
        "alternate_routes": 2,
    }
    resp = integration_client.post("/v1/plan", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert len(data["routes"]) >= 1
    primary = data["routes"][0]
    assert primary["distance_m"] > 0
    assert primary["duration_s"] > 0
    assert primary["dose"] > 0
    assert primary["mean_aqi"] > 0
    assert primary["route_label"] in ["Direct", "Max Clean Air", "Balanced"]
    assert primary["confidence"] in ["high", "medium", "low"]
    assert isinstance(data["best_windows"], list)


def test_integration_airquality_flow(integration_client: TestClient) -> None:
    """Test full /v1/airquality live single-point probe."""
    resp = integration_client.get("/v1/airquality?lat=28.6139&lng=77.2090")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["coordinate"]["lat"] == pytest.approx(28.6139, abs=1e-3)
    assert data["coordinate"]["lng"] == pytest.approx(77.2090, abs=1e-3)
    assert data["aqi"] > 0
    assert data["is_national_aqi"] is True
    assert "PM2.5" in data["pollutants"]
    assert isinstance(data["advisory"], str)
    assert len(data["advisory"]) > 0


def test_integration_loop_flow(integration_client: TestClient) -> None:
    """Test full /v1/routes/loop circular loop generation."""
    payload = {
        "origin": {"lat": 28.6139, "lng": 77.2090},
        "target_distance_km": 3.5,
        "activity": Activity.RUN.value,
        "sensitivity": Sensitivity.NORMAL.value,
        "num_alternatives": 1,
    }
    resp = integration_client.post("/v1/routes/loop", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert isinstance(data, list)
    assert len(data) >= 1
    loop_route = data[0]
    assert loop_route["distance_m"] == pytest.approx(3500.0, rel=0.1)
    assert loop_route["duration_s"] > 0
    assert loop_route["dose"] > 0


def test_integration_router_failure_returns_502() -> None:
    """Verify router failure yields 502 Bad Gateway."""
    grid = _RecordedGridProvider()
    router = _RecordedRouter(should_fail=True)
    service = PlanService(grid, router, street_params=street_model.IDENTITY_MODEL)  # type: ignore[arg-type]
    settings = Settings(xkdr_api_key="k")
    app = create_app(settings, service=service)
    client = TestClient(app)

    payload = {
        "origin": {"lat": 28.6139, "lng": 77.2090},
        "destination": {"lat": 28.6250, "lng": 77.2180},
    }
    resp = client.post("/v1/plan", json=payload)
    assert resp.status_code == 502


def test_integration_grid_failure_returns_503() -> None:
    """Verify missing grid coverage yields 503 Service Unavailable."""
    grid = _FailingGridProvider()
    router = _RecordedRouter()
    service = PlanService(grid, router, street_params=street_model.IDENTITY_MODEL)  # type: ignore[arg-type]
    settings = Settings(xkdr_api_key="k")
    app = create_app(settings, service=service)
    client = TestClient(app)

    payload = {
        "origin": {"lat": 28.6139, "lng": 77.2090},
        "destination": {"lat": 28.6250, "lng": 77.2180},
    }
    resp = client.post("/v1/plan", json=payload)
    assert resp.status_code == 503
