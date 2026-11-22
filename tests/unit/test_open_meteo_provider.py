"""Open-Meteo provider: live CAMS concentrations.

Two properties matter and are easy to get wrong:

* **CO arrives in µg/m³** and the CPCB anchors are mg/m³. Unconverted, a normal
  1438 µg/m³ reading saturates the sub-index at 500 and reports "Severe".
* **Multiple coordinates return a list**, one entry per point, in request order.
  Mismatching that order silently attributes one point's air to another.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from aeropath.domain import cpcb_aqi
from aeropath.external.open_meteo_provider import OpenMeteoProvider
from aeropath.schemas.common import Confidence, Coordinate

BASE = "https://air-quality-api.open-meteo.com"

DELHI = Coordinate(lat=28.6315, lng=77.2167)
NOIDA = Coordinate(lat=28.5355, lng=77.3910)


def _entry(
    lat: float,
    lng: float,
    *,
    pm25: float = 60.0,
    co: float = 1438.0,
    time: str = "2026-11-21T01:30",
) -> dict[str, object]:
    return {
        "latitude": lat,
        "longitude": lng,
        "current": {
            "time": time,
            "pm2_5": pm25,
            "pm10": 70.0,
            "nitrogen_dioxide": 40.0,
            "sulphur_dioxide": 20.0,
            "carbon_monoxide": co,
            "ozone": 30.0,
        },
    }


class TestFetching:
    @respx.mock
    async def test_single_point_returns_an_object_not_a_list(self) -> None:
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(200, json=_entry(DELHI.lat, DELHI.lng))
        )
        async with OpenMeteoProvider() as provider:
            readings = await provider.concentrations_at([DELHI])
        assert len(readings) == 1
        assert readings[0].concentrations

    @respx.mock
    async def test_multiple_points_keep_request_order(self) -> None:
        """Swapped order would attribute one place's air to another."""
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _entry(DELHI.lat, DELHI.lng, pm25=100.0),
                    _entry(NOIDA.lat, NOIDA.lng, pm25=50.0),
                ],
            )
        )
        async with OpenMeteoProvider() as provider:
            readings = await provider.concentrations_at([DELHI, NOIDA])

        assert readings[0].coordinate.lat == pytest.approx(DELHI.lat)
        assert readings[0].concentrations["PM2.5"] == pytest.approx(100.0)
        assert readings[1].coordinate.lat == pytest.approx(NOIDA.lat)
        assert readings[1].concentrations["PM2.5"] == pytest.approx(50.0)

    @respx.mock
    async def test_http_error_yields_empty_readings_not_an_exception(self) -> None:
        respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(500))
        async with OpenMeteoProvider() as provider:
            readings = await provider.concentrations_at([DELHI])
        assert readings[0].concentrations == {}
        assert readings[0].nearest_station_km == float("inf")

    @respx.mock
    async def test_empty_input_makes_no_request(self) -> None:
        route = respx.get(f"{BASE}/v1/air-quality")
        async with OpenMeteoProvider() as provider:
            assert await provider.concentrations_at([]) == []
        assert not route.called


class TestUnitsAndOddities:
    @respx.mock
    async def test_co_is_converted_from_micrograms(self) -> None:
        """The trap: 1438 µg/m³ is 1.438 mg/m³, not a 500 sub-index."""
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(200, json=_entry(DELHI.lat, DELHI.lng, co=1438.0))
        )
        async with OpenMeteoProvider() as provider:
            reading = (await provider.concentrations_at([DELHI]))[0]

        assert reading.concentrations["CO"] == pytest.approx(1.438)
        sub = cpcb_aqi.sub_index("CO", reading.concentrations["CO"])
        assert sub < 200.0

    @respx.mock
    async def test_six_pollutants_make_a_national_aqi(self) -> None:
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(200, json=_entry(DELHI.lat, DELHI.lng))
        )
        async with OpenMeteoProvider() as provider:
            reading = (await provider.concentrations_at([DELHI]))[0]

        result = cpcb_aqi.compute(reading.concentrations)
        assert result.is_national is True
        assert result.pollutant_count == 6

    @respx.mock
    async def test_reports_model_cell_offset(self) -> None:
        """The grid snaps to a cell centre; the distance is the resolution."""
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(200, json=_entry(28.599998, 77.20001))
        )
        async with OpenMeteoProvider() as provider:
            await provider.concentrations_at([DELHI])
        assert provider.resolution_km is not None
        assert provider.resolution_km > 0

    @respx.mock
    async def test_confidence_is_low_because_the_grid_is_coarse(self) -> None:
        """A 45 km model must never present as street-level confidence."""
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(200, json=_entry(DELHI.lat, DELHI.lng))
        )
        async with OpenMeteoProvider() as provider:
            reading = (await provider.concentrations_at([DELHI]))[0]
        assert reading.confidence is Confidence.LOW

    @respx.mock
    async def test_captures_observed_time(self) -> None:
        respx.get(f"{BASE}/v1/air-quality").mock(
            return_value=httpx.Response(
                200, json=_entry(DELHI.lat, DELHI.lng, time="2026-11-21T01:30")
            )
        )
        async with OpenMeteoProvider() as provider:
            reading = (await provider.concentrations_at([DELHI]))[0]
        assert reading.observed_at is not None
        assert reading.observed_at.year == 2026


class TestHourlyForecast:
    @respx.mock
    async def test_returns_hourly_concentrations(self) -> None:
        payload = {
            "latitude": DELHI.lat,
            "longitude": DELHI.lng,
            "hourly": {
                "time": ["2026-11-21T01:00", "2026-11-21T02:00"],
                "pm2_5": [50.0, 45.0],
                "pm10": [80.0, 70.0],
                "nitrogen_dioxide": [30.0, 25.0],
                "sulphur_dioxide": [15.0, 10.0],
                "carbon_monoxide": [1200.0, 1000.0],
                "ozone": [20.0, 22.0],
            },
        }
        respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(200, json=payload))
        async with OpenMeteoProvider() as provider:
            forecast = await provider.hourly_forecast(DELHI, hours=2)

        assert len(forecast) == 2
        assert forecast[0]["PM2.5"] == pytest.approx(50.0)
        # CO in mg/m³
        assert forecast[0]["CO"] == pytest.approx(1.2)
        assert forecast[1]["PM2.5"] == pytest.approx(45.0)
        assert forecast[1]["CO"] == pytest.approx(1.0)

    @respx.mock
    async def test_handles_http_error(self) -> None:
        respx.get(f"{BASE}/v1/air-quality").mock(return_value=httpx.Response(500))
        async with OpenMeteoProvider() as provider:
            forecast = await provider.hourly_forecast(DELHI, hours=24)
        assert forecast == {}
