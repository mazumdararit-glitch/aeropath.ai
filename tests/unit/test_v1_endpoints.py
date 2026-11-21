"""Tests for V1 endpoints (airquality, loop, profile, places, alerts)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from aeropath.api.deps import get_current_user
from aeropath.api.main import create_app
from aeropath.api.v1.airquality import get_grid_provider
from aeropath.api.v1.routes import get_loop_generator
from aeropath.config import Settings
from aeropath.db.session import get_db
from aeropath.external.osrm import RouteGeometry
from aeropath.external.provider import PointReading
from aeropath.models.place import AlertConfig, SavedPlace
from aeropath.models.user import Profile, User
from aeropath.schemas.common import Coordinate
from aeropath.services.loop_generator import LoopGenerator


class _StubGrid:
    name = "mock_grid"

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        return [
            PointReading(
                coordinate=p,
                concentrations={
                    "PM2.5": 35.0,
                    "PM10": 60.0,
                    "NO2": 20.0,
                    "SO2": 10.0,
                    "CO": 0.5,
                    "O3": 15.0,
                },
                source=self.name,
                nearest_station_km=1.2,
                observed_at=datetime.now(UTC),
            )
            for p in points
        ]


class _StubLoopRouter:
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
                distance_m=3000.0,
                duration_s=2100.0,
            )
        ]


@pytest.fixture
def test_client() -> TestClient:
    settings = Settings(xkdr_api_key="test-key", grid_provider="mock_grid")
    app = create_app(settings)
    app.dependency_overrides[get_grid_provider] = lambda: _StubGrid()
    generator = LoopGenerator(_StubGrid(), _StubLoopRouter())  # type: ignore[arg-type]
    app.dependency_overrides[get_loop_generator] = lambda: generator
    return TestClient(app)


def test_get_current_airquality(test_client: TestClient) -> None:
    resp = test_client.get("/v1/airquality?lat=28.61&lng=77.20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["coordinate"]["lat"] == 28.61
    assert data["coordinate"]["lng"] == 77.20
    assert data["aqi"] > 0
    assert data["category"] in ["good", "satisfactory", "moderate"]
    assert data["is_national_aqi"] is True
    assert "PM2.5" in data["pollutants"] or "pm25" in data["pollutants"]


def test_post_routes_loop(test_client: TestClient) -> None:
    resp = test_client.post(
        "/v1/routes/loop",
        json={
            "origin": {"lat": 28.61, "lng": 77.20},
            "target_distance_km": 3.0,
            "activity": "run",
            "sensitivity": "normal",
            "num_alternatives": 2,
        },
    )
    assert resp.status_code == 200
    routes = resp.json()
    assert len(routes) > 0
    assert routes[0]["distance_m"] == 3000.0
    assert len(routes[0]["geometry"]) > 0


class _FakeScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _FakeResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._items)


class _InMemoryDbSession:
    def __init__(self) -> None:
        self.places: dict[UUID, SavedPlace] = {}
        self.alerts: dict[UUID, AlertConfig] = {}

    def add(self, obj: Any) -> None:
        if isinstance(obj, SavedPlace):
            if not hasattr(obj, "id") or obj.id is None:
                obj.id = uuid.uuid4()
            if not hasattr(obj, "created_at") or obj.created_at is None:
                obj.created_at = datetime.now(UTC)
            self.places[obj.id] = obj
        elif isinstance(obj, AlertConfig):
            if not hasattr(obj, "id") or obj.id is None:
                obj.id = uuid.uuid4()
            if not hasattr(obj, "created_at") or obj.created_at is None:
                obj.created_at = datetime.now(UTC)
            self.alerts[obj.id] = obj

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass

    async def delete(self, obj: Any) -> None:
        if isinstance(obj, SavedPlace):
            self.places.pop(obj.id, None)
        elif isinstance(obj, AlertConfig):
            self.alerts.pop(obj.id, None)

    async def execute(self, stmt: Any) -> _FakeResult:
        # Check target table from stmt
        stmt_str = str(stmt).lower()
        if "saved_places" in stmt_str:
            items = list(self.places.values())
            # Handle where id == ...
            for place_id, p in self.places.items():
                if str(place_id) in str(stmt):
                    return _FakeResult([p])
            return _FakeResult(items)
        if "alert_configs" in stmt_str:
            return _FakeResult(list(self.alerts.values()))
        return _FakeResult([])


@pytest.mark.asyncio
async def test_user_and_places_crud() -> None:
    session = _InMemoryDbSession()

    async def override_get_db() -> AsyncGenerator[Any, None]:
        yield session

    test_user_id = uuid.uuid4()
    dummy_user = User(
        id=test_user_id,
        auth_id="test-auth-001",
        email="developer@aeropath.local",
        language="en",
        created_at=datetime.now(UTC),
    )
    dummy_profile = Profile(
        id=uuid.uuid4(),
        user_id=test_user_id,
        sensitivity="normal",
        activity_type="walk",
    )
    dummy_user.profile = dummy_profile

    async def override_get_current_user() -> User:
        return dummy_user

    settings = Settings(xkdr_api_key="test-key")
    app = create_app(settings)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        # 1. Profile GET
        resp = client.get("/v1/profile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "developer@aeropath.local"
        assert data["sensitivity"] == "normal"

        # 2. Profile PUT
        resp = client.put(
            "/v1/profile",
            json={"sensitivity": "sensitive", "default_activity": "run"},
        )
        assert resp.status_code == 200
        assert resp.json()["sensitivity"] == "sensitive"
        assert resp.json()["default_activity"] == "run"

        # 3. Places POST
        resp = client.post(
            "/v1/places",
            json={
                "name": "Central Park",
                "lat": 28.61,
                "lng": 77.20,
                "alert_threshold": 120.0,
            },
        )
        assert resp.status_code == 201
        place = resp.json()
        place_id = place["id"]
        assert place["name"] == "Central Park"

        # 4. Places GET
        resp = client.get("/v1/places")
        assert resp.status_code == 200
        places = resp.json()
        assert len(places) == 1
        assert places[0]["id"] == place_id

        # 5. Alerts POST
        resp = client.post(
            "/v1/alerts",
            json={
                "lat": 28.61,
                "lng": 77.20,
                "threshold_aqi": 150.0,
                "place_id": place_id,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["threshold_aqi"] == 150.0

        # 6. Alerts GET
        resp = client.get("/v1/alerts")
        assert resp.status_code == 200
        alerts = resp.json()
        assert len(alerts) == 1

        # 7. Places DELETE
        resp = client.delete(f"/v1/places/{place_id}")
        assert resp.status_code == 204
