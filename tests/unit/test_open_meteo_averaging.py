"""Tests for CPCB pollutant averaging in Open-Meteo provider (Sprint 1 - A1)."""

from __future__ import annotations

import httpx
import pytest
import respx

from aeropath.domain import cpcb_aqi
from aeropath.external.open_meteo_provider import OpenMeteoProvider
from aeropath.schemas.common import AQCategory, Coordinate

BASE = "https://air-quality-api.open-meteo.com"
DELHI = Coordinate(lat=28.6315, lng=77.2167)


def _make_response_payload(
    *,
    has_hourly: bool = True,
    pm25_series: list[float] | None = None,
    ozone_series: list[float] | None = None,
    current_pm25: float = 120.0,
    current_ozone: float = 140.0,
) -> dict[str, object]:
    times = [f"2026-11-21T{h:02d}:00" for h in range(25)]
    if pm25_series is None:
        pm25_series = [120.0] * 25
    if ozone_series is None:
        # Midday spike at the end, but low earlier (night/morning)
        ozone_series = [30.0] * 17 + [40.0, 50.0, 60.0, 80.0, 100.0, 120.0, 130.0, 140.0]

    payload: dict[str, object] = {
        "latitude": DELHI.lat,
        "longitude": DELHI.lng,
        "current": {
            "time": "2026-11-21T24:00",
            "pm2_5": current_pm25,
            "pm10": 180.0,
            "nitrogen_dioxide": 45.0,
            "sulphur_dioxide": 15.0,
            "carbon_monoxide": 1500.0,
            "ozone": current_ozone,
        },
    }
    if has_hourly:
        payload["hourly"] = {
            "time": times,
            "pm2_5": pm25_series,
            "pm10": [180.0] * 25,
            "nitrogen_dioxide": [45.0] * 25,
            "sulphur_dioxide": [15.0] * 25,
            "carbon_monoxide": [1500.0] * 25,
            "ozone": ozone_series,
        }
    return payload


@respx.mock
async def test_rolling_averages_computed_from_history() -> None:
    """Ozone should be 8-hour mean, PM2.5 24-hour mean."""
    # Last 8 hours ozone: [40, 50, 60, 80, 100, 120, 130, 140] -> sum = 720 / 8 = 90.0
    payload = _make_response_payload(has_hourly=True)
    respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(200, json=payload))

    async with OpenMeteoProvider() as provider:
        readings = await provider.concentrations_at([DELHI])

    assert len(readings) == 1
    reading = readings[0]
    assert reading.is_estimated is False
    assert reading.concentrations["Ozone"] == pytest.approx(90.0)
    assert reading.concentrations["PM2.5"] == pytest.approx(120.0)


@respx.mock
async def test_winter_fixture_pm25_becomes_dominant() -> None:
    """In winter, high midday 1-h ozone must NOT eclipse 24-h severe PM2.5."""
    # 24h PM2.5 is 220 µg/m³ (Severe).
    # 1h Ozone is 140 µg/m³, but 8h rolling mean is only 45 µg/m³ (Satisfactory/Good).
    pm25_series = [220.0] * 25
    ozone_series = [20.0] * 17 + [30.0, 35.0, 40.0, 45.0, 50.0, 60.0, 70.0, 140.0]
    # last 8 ozone: 30 + 35 + 40 + 45 + 50 + 60 + 70 + 140 = 470 / 8 = 58.75

    payload = _make_response_payload(
        has_hourly=True,
        pm25_series=pm25_series,
        ozone_series=ozone_series,
        current_pm25=220.0,
        current_ozone=140.0,
    )
    respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(200, json=payload))

    async with OpenMeteoProvider() as provider:
        readings = await provider.concentrations_at([DELHI])

    reading = readings[0]
    aqi_res = cpcb_aqi.compute(reading.concentrations)
    # PM2.5 sub-index for 220 µg/m³ is > 300 (Very Poor / Severe)
    # Ozone sub-index for 58.75 µg/m³ is ~58 (Satisfactory)
    assert aqi_res.dominant_pollutant == "PM2.5"
    assert aqi_res.category in (AQCategory.VERY_POOR, AQCategory.SEVERE)


@respx.mock
async def test_fallback_when_hourly_history_missing() -> None:
    """When no hourly series exists, 1-hour ozone is damped (x0.65) and marked estimated."""
    payload = _make_response_payload(has_hourly=False, current_ozone=100.0)
    respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(200, json=payload))

    async with OpenMeteoProvider() as provider:
        readings = await provider.concentrations_at([DELHI])

    reading = readings[0]
    assert reading.is_estimated is True
    # 100.0 * 0.65 = 65.0 µg/m³
    assert reading.concentrations["Ozone"] == pytest.approx(65.0)


@respx.mock
async def test_completeness_rule_enforced() -> None:
    """If fewer than 16 hours available for 24-h PM2.5, mark as estimated."""
    # Only 10 hours of PM2.5 data provided
    pm25_series: list[float | None] = [None] * 15 + [120.0] * 10  # type: ignore[list-item]
    payload = _make_response_payload(has_hourly=True)
    payload["hourly"]["pm2_5"] = pm25_series  # type: ignore[index]
    respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(200, json=payload))

    async with OpenMeteoProvider() as provider:
        readings = await provider.concentrations_at([DELHI])

    reading = readings[0]
    # Gaps cause completeness failure -> fallback to estimated
    assert reading.is_estimated is True
