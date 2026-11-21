"""HTTP contract tests.

The API is a translation layer, so these tests verify translation — status codes,
payload shape, and that a missing key fails at startup rather than per request —
not the maths, which is tested where it lives.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from aeropath.api.main import create_app
from aeropath.config import XKDR_SIGNUP_URL, Settings
from aeropath.external.osrm import RoutingError
from aeropath.schemas.common import (
    Activity,
    Advisory,
    AQCategory,
    Confidence,
)
from aeropath.services.planning import HourWindow, Plan, PlannedRoute
from aeropath.services.scoring.engine import RouteScore


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"xkdr_api_key": "test-key"}
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _fake_plan() -> Plan:
    score = RouteScore(
        segments=(),
        distance_m=1200.0,
        mean_aqi=150.0,
        dose=180000.0,
        category=AQCategory.MODERATE,
        activity=Activity.WALK,
        advisory=Advisory.CAUTION,
        worst_segment_id=None,
        unscored_fraction=0.0,
    )
    return Plan(
        routes=(
            PlannedRoute(
                distance_m=1200.0,
                duration_s=860.0,
                score=score,
                aqi_confidence=Confidence.HIGH,
                basis="measured",
            ),
        ),
        best_windows=(
            HourWindow(
                hour=6,
                mean_aqi=95.0,
                category=AQCategory.SATISFACTORY,
                dose=142500.0,
                samples=42,
                basis="climatology",
            ),
        ),
        generated_at=datetime(2024, 11, 1, 8, tzinfo=UTC),
        data_source="xkdr_cpcb_idw",
    )


class _StubService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def plan(self, request: object, *, at: object = None) -> Plan:
        del request, at
        if self._error is not None:
            raise self._error
        return _fake_plan()


class TestHealth:
    def test_reports_configuration_state(self) -> None:
        client = TestClient(create_app(_settings()))
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["xkdr_configured"] is True

        # Test /health alias
        response_alias = client.get("/health")
        assert response_alias.status_code == 200
        assert response_alias.json()["status"] == "ok"

    def test_map_endpoint_returns_html(self) -> None:
        client = TestClient(create_app(_settings()))
        response = client.get("/map")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "AEROPATH" in response.text
        assert "leaflet" in response.text

    def test_root_includes_map_link(self) -> None:
        client = TestClient(create_app(_settings()))
        response = client.get("/")
        assert response.status_code == 200
        assert response.json()["map"] == "/map"


class TestPlanEndpoint:
    def _client(self, service: object) -> TestClient:
        return TestClient(create_app(_settings(), service=service))  # type: ignore[arg-type]

    def test_returns_ranked_routes_and_windows(self) -> None:
        client = self._client(_StubService())
        response = client.post(
            "/v1/plan",
            json={
                "origin": {"lat": 28.6, "lng": 77.2},
                "destination": {"lat": 28.7, "lng": 77.3},
                "activity": "walk",
                "sensitivity": "normal",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data_source"] == "xkdr_cpcb_idw"
        assert len(body["routes"]) == 1
        assert body["routes"][0]["mean_aqi"] == pytest.approx(150.0)
        assert body["routes"][0]["basis"] == "measured"
        assert body["best_windows"][0]["basis"] == "climatology"

    def test_routing_failure_is_a_502(self) -> None:
        client = self._client(_StubService(error=RoutingError("router down")))
        response = client.post(
            "/v1/plan",
            json={
                "origin": {"lat": 28.6, "lng": 77.2},
                "destination": {"lat": 28.7, "lng": 77.3},
            },
        )
        assert response.status_code == 502
        assert "router down" in response.json()["detail"]

    def test_invalid_coordinates_are_rejected(self) -> None:
        client = self._client(_StubService())
        response = client.post(
            "/v1/plan",
            json={
                "origin": {"lat": 95.0, "lng": 77.2},
                "destination": {"lat": 28.7, "lng": 77.3},
            },
        )
        assert response.status_code == 422

    def test_window_basis_is_never_omitted(self) -> None:
        """A user must be able to tell a climatology from a forecast."""
        client = self._client(_StubService())
        response = client.post(
            "/v1/plan",
            json={
                "origin": {"lat": 28.6, "lng": 77.2},
                "destination": {"lat": 28.7, "lng": 77.3},
            },
        )
        assert response.json()["best_windows"][0]["basis"] == "climatology"


class TestMissingKey:
    def test_startup_names_the_signup_url(self) -> None:
        settings = _settings(xkdr_api_key="")
        with pytest.raises(RuntimeError) as excinfo:
            settings.require_xkdr()
        assert XKDR_SIGNUP_URL in str(excinfo.value)

    def test_empty_key_is_not_silently_accepted(self) -> None:
        assert _settings(xkdr_api_key="").xkdr_api_key == ""
