"""Tests for Task A4: Provider fallback chain, budget, caching, and health."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from aeropath.api.main import create_app
from aeropath.config import Settings
from aeropath.external.google_aq_client import GoogleAirQualityProvider, HourlyCell
from aeropath.external.provider import PointReading
from aeropath.schemas.common import Coordinate
from aeropath.services.planning import PlanService
from aeropath.services.provider_chain import FallbackProviderChain
from aeropath.services.scoring import street_model


class _DummyFailingProvider:
    name: str = "failing_primary"

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        raise RuntimeError("Primary provider connection refused")

    async def aclose(self) -> None:
        pass


class _DummyWorkingProvider:
    def __init__(self, name: str = "working_secondary") -> None:
        self.name: str = name
        self.calls = 0

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        self.calls += 1
        return [
            PointReading(
                coordinate=p,
                concentrations={"PM2.5": 28.0, "PM10": 55.0},
                source=self.name,
                nearest_station_km=1.0,
                observed_at=datetime.now(UTC),
            )
            for p in points
        ]

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_fallback_on_primary_failure() -> None:
    """Chain falls back to working secondary when primary fails."""
    primary = _DummyFailingProvider()
    secondary = _DummyWorkingProvider()
    chain = FallbackProviderChain([primary, secondary], request_budget_limit=100)

    points = [Coordinate(lat=28.61, lng=77.20)]
    readings = await chain.concentrations_at(points)

    assert len(readings) == 1
    assert readings[0].source == "working_secondary"
    assert chain.active_provider_name == "working_secondary"
    assert "failing_primary" in (chain.last_error or "")


@pytest.mark.asyncio
async def test_budget_exhaustion_triggers_fallback() -> None:
    """When budget limit is reached, primary is skipped and secondary is used."""
    primary = _DummyWorkingProvider("budgeted_primary")
    secondary = _DummyWorkingProvider("fallback_secondary")
    chain = FallbackProviderChain([primary, secondary], request_budget_limit=1)

    points = [Coordinate(lat=28.61, lng=77.20)]
    # First call uses primary
    res1 = await chain.concentrations_at(points)
    assert res1[0].source == "budgeted_primary"
    assert chain.requests_made == 1

    # Second call exceeds budget of 1, so falls back to secondary
    points2 = [Coordinate(lat=28.62, lng=77.21)]
    res2 = await chain.concentrations_at(points2)
    assert res2[0].source == "fallback_secondary"


@pytest.mark.asyncio
async def test_cache_hit_avoids_provider_calls() -> None:
    """Repeated coordinates return from cache without re-querying provider."""
    provider = _DummyWorkingProvider("cached_source")
    chain = FallbackProviderChain([provider], cache_ttl_s=3600)

    points = [Coordinate(lat=28.61, lng=77.20)]
    await chain.concentrations_at(points)
    assert provider.calls == 1

    # Second call with same coordinate should hit cache
    await chain.concentrations_at(points)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_all_providers_fail_raises_runtime_error() -> None:
    """When all providers in chain fail, a RuntimeError is raised."""
    failing1 = _DummyFailingProvider()
    failing2 = _DummyFailingProvider()
    chain = FallbackProviderChain([failing1, failing2])

    points = [Coordinate(lat=28.61, lng=77.20)]
    with pytest.raises(RuntimeError, match="All providers in fallback chain failed"):
        await chain.concentrations_at(points)


class _MockGoogleAQClient:
    async def fetch_current(self, coordinate: Coordinate, *, cell: str) -> HourlyCell:
        return HourlyCell(
            cell=cell,
            hour=datetime.now(UTC),
            aqi=145.0,
            category="MODERATE",
            dominant_pollutant="pm25",
            pollutants={"pm25": 45.0, "pm10": 90.0, "no2": 25.0},
        )

    async def __aenter__(self) -> _MockGoogleAQClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_google_aq_provider_adapter() -> None:
    """GoogleAirQualityProvider correctly translates client results into PointReadings."""
    client = _MockGoogleAQClient()
    provider = GoogleAirQualityProvider("dummy-key", client=client)  # type: ignore[arg-type]

    points = [Coordinate(lat=28.61, lng=77.20)]
    readings = await provider.concentrations_at(points)

    assert len(readings) == 1
    assert readings[0].source == "google_air_quality"
    assert readings[0].nearest_station_km == 0.5
    assert readings[0].concentrations["PM2.5"] == 45.0
    assert readings[0].concentrations["PM10"] == 90.0


def test_provider_health_endpoint() -> None:
    """Health endpoint reports active provider, fallback chain, and request budget."""
    working = _DummyWorkingProvider("test_provider")
    chain = FallbackProviderChain([working], request_budget_limit=500)
    service = PlanService(chain, None, street_params=street_model.IDENTITY_MODEL)  # type: ignore[arg-type]

    settings = Settings(xkdr_api_key="k")
    app = create_app(settings, service=service)
    client = TestClient(app)

    resp = client.get("/v1/providers/health")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] in ["ok", "healthy"]
    assert data["active_provider"] == "test_provider"
    assert data["fallback_chain"] == ["test_provider"]
    assert data["request_budget"]["limit"] == 500
    assert data["request_budget"]["remaining"] == 500
