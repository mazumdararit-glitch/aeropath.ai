"""Coverage discovery and the partial-index fallback.

Two facts this module pins, both discovered against the live API:

* The dataset answers a window past its coverage with **HTTP 500**, not an empty
  result, so "ask for today" fails opaquely instead of returning nothing.
* After 2024-12 the public dataset reports only PM2.5 and PM10, which is below
  CPCB's three-pollutant minimum, so a national AQI is uncomputable and the
  product must fall back to a labelled particulate sub-index.
"""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest
import respx

from aeropath.domain import cpcb_aqi
from aeropath.external.cpcb_stations import (
    DataCoverage,
    StationClient,
    StationDataError,
)

BASE = "https://airquality.xkdr.org"


def _measurement(
    station: str, parameter: str, hour: str = "2025-08-15T00:00:00"
) -> dict[str, object]:
    return {
        "station_id": station,
        "parameter_name": parameter,
        "unit": "µg/m³",
        "period_start": hour,
        "mean": 40.0,
        "n": 1,
    }


class TestCoverage:
    @respx.mock
    async def test_reads_last_month_from_meta(self) -> None:
        respx.get(f"{BASE}/v1/meta").mock(
            return_value=httpx.Response(
                200,
                json={
                    "exported_at": "2026-09-30T11:25:11+00:00",
                    "measurements": {"first_month": "2009-01", "last_month": "2026-03"},
                },
            )
        )
        async with StationClient() as client:
            window = await client.coverage()
        assert window.first_month == "2009-01"
        assert window.last_month == "2026-03"
        assert window.last_month_date == (2026, 3)

    @respx.mock
    async def test_meta_failure_is_a_station_error(self) -> None:
        respx.get(f"{BASE}/v1/meta").mock(return_value=httpx.Response(500))
        async with StationClient() as client:
            with pytest.raises(StationDataError, match="meta failed"):
                await client.coverage()

    def test_last_month_date_is_none_when_malformed(self) -> None:
        assert DataCoverage("x", "not-a-month", "y").last_month_date is None


class TestOutOfCoverage:
    @respx.mock
    async def test_500_becomes_a_distinct_out_of_coverage_signal(self) -> None:
        """The regression: a past-coverage window is a 500, not an empty list."""
        respx.get(f"{BASE}/v1/measurements").mock(
            return_value=httpx.Response(500, json={"error": "internal_error"})
        )
        async with StationClient() as client:
            with pytest.raises(StationDataError, match="past the dataset's coverage"):
                await client.measurements(
                    city="Delhi",
                    parameters=["PM2.5"],
                    start=__import__("datetime").date(2026, 11, 1),
                    end=__import__("datetime").date(2026, 11, 1),
                )


class TestLatestAvailableDay:
    @respx.mock
    async def test_finds_a_day_with_at_least_two_parameters(self) -> None:
        respx.get(f"{BASE}/v1/meta").mock(
            return_value=httpx.Response(
                200, json={"measurements": {"first_month": "2009-01", "last_month": "2026-03"}}
            )
        )

        def responder(request: httpx.Request) -> httpx.Response:
            start = request.url.params.get("start", "")
            if start.startswith("2026-03"):
                # Newest month exists but only reports one pollutant: unusable.
                return httpx.Response(200, json={"meta": {"rows": 1, "truncated": False},
                                                 "data": [_measurement("s1", "PM2.5")]})
            return httpx.Response(
                200,
                json={
                    "meta": {"rows": 2, "truncated": False},
                    "data": [_measurement("s1", "PM2.5"), _measurement("s1", "PM10")],
                },
            )

        respx.get(f"{BASE}/v1/measurements").mock(side_effect=responder)
        async with StationClient() as client:
            day = await client.latest_available_day(city="Delhi")
        assert day is not None
        assert day.month != 3

    @respx.mock
    async def test_returns_none_when_nothing_usable(self) -> None:
        respx.get(f"{BASE}/v1/meta").mock(
            return_value=httpx.Response(
                200, json={"measurements": {"first_month": "2009-01", "last_month": "2026-03"}}
            )
        )
        respx.get(f"{BASE}/v1/measurements").mock(
            return_value=httpx.Response(
                200, json={"meta": {"rows": 0, "truncated": False}, "data": []}
            )
        )
        async with StationClient() as client:
            assert await client.latest_available_day(city="Delhi") is None


class TestPartialIndexFallback:
    """Two pollutants cannot make a national AQI, but they can rank routes."""

    TWO_POLLUTANTS: ClassVar[dict[str, float]] = {"PM2.5": 37.2, "PM10": 63.8}
    THREE_POLLUTANTS: ClassVar[dict[str, float]] = {
        "PM2.5": 37.2,
        "PM10": 63.8,
        "NO2": 45.0,
    }

    def test_two_pollutants_raise_under_the_national_rule(self) -> None:
        with pytest.raises(cpcb_aqi.InsufficientDataError, match="needs 3"):
            cpcb_aqi.compute(self.TWO_POLLUTANTS)

    def test_two_pollutants_still_yield_an_index_when_allowed(self) -> None:
        result = cpcb_aqi.compute(self.TWO_POLLUTANTS, require_minimum=False)
        assert result.aqi > 0
        assert result.is_national is False

    def test_three_pollutants_are_national(self) -> None:
        result = cpcb_aqi.compute(self.THREE_POLLUTANTS)
        assert result.is_national is True

    def test_is_national_requires_a_particulate(self) -> None:
        gases_only = {"NO2": 45.0, "SO2": 20.0, "CO": 1.0}
        result = cpcb_aqi.compute(gases_only, require_minimum=False)
        assert result.is_national is False
