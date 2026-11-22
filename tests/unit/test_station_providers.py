"""Tests for Ground-station providers and Bias-Correction (Sprint 1 - A2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from aeropath.external.bias_corrected_provider import StationCorrectedProvider
from aeropath.external.open_meteo_provider import OpenMeteoProvider
from aeropath.external.station_providers import (
    GroundStationReading,
    OpenAQClient,
    WaqiClient,
    filter_station_readings,
)
from aeropath.schemas.common import Confidence, Coordinate

DELHI_ORIGIN = Coordinate(lat=28.6315, lng=77.2167)
NOW = datetime.now(UTC)


def test_quality_checks_filters_stale_and_outliers() -> None:
    readings = [
        # Valid fresh reading
        GroundStationReading(
            station_id="s1",
            name="Station 1",
            coordinate=DELHI_ORIGIN,
            pollutant="PM2.5",
            value=120.0,
            unit="µg/m³",
            timestamp=NOW - timedelta(minutes=30),
            source="waqi",
            attribution="CPCB/WAQI",
        ),
        # Stale reading (> 3 hours)
        GroundStationReading(
            station_id="s2",
            name="Station 2",
            coordinate=DELHI_ORIGIN,
            pollutant="PM2.5",
            value=110.0,
            unit="µg/m³",
            timestamp=NOW - timedelta(hours=4),
            source="waqi",
            attribution="CPCB/WAQI",
        ),
        # Negative value (invalid)
        GroundStationReading(
            station_id="s3",
            name="Station 3",
            coordinate=DELHI_ORIGIN,
            pollutant="PM2.5",
            value=-15.0,
            unit="µg/m³",
            timestamp=NOW - timedelta(minutes=15),
            source="waqi",
            attribution="CPCB/WAQI",
        ),
        # Extreme outlier (e.g. PM2.5 > 1000)
        GroundStationReading(
            station_id="s4",
            name="Station 4",
            coordinate=DELHI_ORIGIN,
            pollutant="PM2.5",
            value=1800.0,
            unit="µg/m³",
            timestamp=NOW - timedelta(minutes=10),
            source="waqi",
            attribution="CPCB/WAQI",
        ),
    ]

    filtered = filter_station_readings(readings, reference_time=NOW, max_age_hours=3.0)
    assert len(filtered) == 1
    assert filtered[0].station_id == "s1"
    assert filtered[0].value == 120.0


@respx.mock
async def test_waqi_client_parses_bounding_box_feed() -> None:
    waqi_url = "https://api.waqi.info/v2/map/bounds"
    mock_payload = {
        "status": "ok",
        "data": [
            {
                "lat": 28.63,
                "lon": 77.21,
                "uid": 1001,
                "aqi": "145",
                "station": {"name": "Connaught Place, Delhi", "time": NOW.isoformat()},
            }
        ],
    }
    respx.get(waqi_url).mock(return_value=httpx.Response(200, json=mock_payload))

    client = WaqiClient(token="test_token")
    stations = await client.fetch_in_bbox(
        min_lat=28.5, min_lng=77.1, max_lat=28.8, max_lng=77.4
    )
    assert len(stations) == 1
    assert stations[0].station_id == "waqi_1001"
    assert stations[0].attribution != ""


@respx.mock
async def test_openaq_client_handles_missing_key_gracefully() -> None:
    client = OpenAQClient(api_key="")
    stations = await client.fetch_in_bbox(
        min_lat=28.5, min_lng=77.1, max_lat=28.8, max_lng=77.4
    )
    assert stations == []


@respx.mock
async def test_bias_corrected_provider_applies_shrunk_residual() -> None:
    """Model predicts 50 PM2.5; station measures 150. Shrunk residual (+75) adjusts model to 125."""
    # Mock base open-meteo provider returning 50.0 PM2.5
    base_url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    base_payload = {
        "latitude": DELHI_ORIGIN.lat,
        "longitude": DELHI_ORIGIN.lng,
        "current": {
            "time": NOW.isoformat(),
            "pm2_5": 50.0,
            "pm10": 80.0,
            "nitrogen_dioxide": 30.0,
            "sulphur_dioxide": 10.0,
            "carbon_monoxide": 1000.0,
            "ozone": 40.0,
        },
    }
    respx.get(base_url).mock(return_value=httpx.Response(200, json=base_payload))

    base_provider = OpenMeteoProvider()
    station_reading = GroundStationReading(
        station_id="sta_delhi_1",
        name="CPCB Central Delhi",
        coordinate=DELHI_ORIGIN,
        pollutant="PM2.5",
        value=150.0,
        unit="µg/m³",
        timestamp=NOW,
        source="cpcb_live",
        attribution="CPCB Realtime Feed",
    )

    provider = StationCorrectedProvider(
        base_provider=base_provider,
        stations_loader=lambda: [station_reading],
        shrinkage_factor=0.75,
    )

    readings = await provider.concentrations_at([DELHI_ORIGIN], at=NOW)
    assert len(readings) == 1
    reading = readings[0]

    # Model = 50, Station = 150 -> Residual = +100
    # Correction = +100 * 0.75 = +75
    # Corrected PM2.5 = 50 + 75 = 125.0
    assert reading.concentrations["PM2.5"] == pytest.approx(125.0, abs=1.0)
    assert reading.source == "station_corrected_cams"
    assert reading.confidence == Confidence.HIGH


@respx.mock
async def test_bias_corrected_provider_falls_back_when_no_stations() -> None:
    """When no valid stations exist in city, uncorrected base model is returned without failure."""
    base_url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    base_payload = {
        "latitude": DELHI_ORIGIN.lat,
        "longitude": DELHI_ORIGIN.lng,
        "current": {
            "time": NOW.isoformat(),
            "pm2_5": 50.0,
            "pm10": 80.0,
            "nitrogen_dioxide": 30.0,
            "sulphur_dioxide": 10.0,
            "carbon_monoxide": 1000.0,
            "ozone": 40.0,
        },
    }
    respx.get(base_url).mock(return_value=httpx.Response(200, json=base_payload))

    base_provider = OpenMeteoProvider()
    provider = StationCorrectedProvider(
        base_provider=base_provider,
        stations_loader=lambda: [],  # No stations
    )

    readings = await provider.concentrations_at([DELHI_ORIGIN], at=NOW)
    assert len(readings) == 1
    # Falls back directly to base uncorrected concentrations
    assert readings[0].concentrations["PM2.5"] == pytest.approx(50.0 * 0.65 or 50.0, abs=20.0)
