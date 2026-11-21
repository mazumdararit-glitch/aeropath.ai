"""Google Air Quality client request and parse behaviour.

The provider gate is only meaningful if the client asks the right endpoint with
the right window. These tests pin the wire contract without any network access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from aeropath.external.google_aq_client import (
    FORECAST_MAX_HOURS,
    HISTORY_MAX_HOURS,
    HISTORY_MAX_PAGE_SIZE,
    INDEX_CODE,
    AirQualityProviderError,
    GoogleAirQualityClient,
)
from aeropath.schemas.common import Coordinate

KOLKATA = Coordinate(lat=22.5726, lng=88.3639)


def _hour_info(hour: str, *, aqi: float = 120.0, code: str = INDEX_CODE) -> dict[str, object]:
    return {
        "dateTime": hour,
        "indexes": [
            {"code": "uaqi", "aqi": 55, "category": "Fair"},
            {"code": code, "aqi": aqi, "category": "Moderate", "dominantPollutant": "pm25"},
        ],
        "pollutants": [
            {
                "code": "pm25",
                "concentration": {"value": 61.0, "units": "MICROGRAMS_PER_CUBIC_METER"},
            }
        ],
    }


class TestHistoryEndpoint:
    @respx.mock
    async def test_posts_to_history_lookup_not_forecast(self) -> None:
        route = respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(
                200, json={"hoursInfo": [_hour_info("2024-11-01T00:00:00Z")]}
            )
        )
        async with GoogleAirQualityClient("key") as client:
            await client.fetch_history(KOLKATA, cell="h3-1", hours=24)

        assert route.called
        body = json.loads(route.calls[0].request.content)
        assert "period" not in body
        assert body["hours"] == 24

    @respx.mock
    async def test_pins_ind_cpcb_and_disables_universal(self) -> None:
        route = respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(
                200, json={"hoursInfo": [_hour_info("2024-11-01T00:00:00Z")]}
            )
        )
        async with GoogleAirQualityClient("key") as client:
            await client.fetch_history(KOLKATA, cell="h3-1", hours=24)

        body = json.loads(route.calls[0].request.content)
        assert body["customLocalAqis"] == [{"regionCode": "IN", "aqi": INDEX_CODE}]
        assert body["universalAqi"] is False

    @respx.mock
    async def test_accepts_explicit_period(self) -> None:
        route = respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(200, json={"hoursInfo": []})
        )
        start = datetime(2024, 11, 1, tzinfo=UTC)
        end = datetime(2024, 11, 2, tzinfo=UTC)
        async with GoogleAirQualityClient("key") as client:
            await client.fetch_history(KOLKATA, cell="h3-1", start=start, end=end)

        body = json.loads(route.calls[0].request.content)
        assert "hours" not in body
        assert body["period"]["startTime"].startswith("2024-11-01")

    @respx.mock
    async def test_rejects_window_over_thirty_days_before_sending(self) -> None:
        route = respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(200, json={"hoursInfo": []})
        )
        async with GoogleAirQualityClient("key") as client:
            with pytest.raises(ValueError, match="caps at"):
                await client.fetch_history(
                    KOLKATA,
                    cell="h3-1",
                    start=datetime(2024, 1, 1, tzinfo=UTC),
                    end=datetime(2024, 3, 1, tzinfo=UTC),
                )
        assert not route.called

    async def test_rejects_hours_over_cap(self) -> None:
        async with GoogleAirQualityClient("key") as client:
            with pytest.raises(ValueError, match="hours"):
                await client.fetch_history(KOLKATA, cell="h3-1", hours=HISTORY_MAX_HOURS + 1)

    async def test_requires_start_and_end_together(self) -> None:
        async with GoogleAirQualityClient("key") as client:
            with pytest.raises(ValueError, match="together"):
                await client.fetch_history(
                    KOLKATA, cell="h3-1", start=datetime(2024, 11, 1, tzinfo=UTC)
                )

    @respx.mock
    async def test_reads_hours_info_key(self) -> None:
        """history uses ``hoursInfo``; reading ``hourlyForecasts`` would return nothing."""
        respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(
                200,
                json={
                    "hoursInfo": [
                        _hour_info("2024-11-01T00:00:00Z", aqi=101.0),
                        _hour_info("2024-11-01T01:00:00Z", aqi=102.0),
                    ]
                },
            )
        )
        async with GoogleAirQualityClient("key") as client:
            fetch = await client.fetch_history(KOLKATA, cell="h3-1", hours=2)

        assert len(fetch.hours) == 2
        assert [h.aqi for h in fetch.hours] == [101.0, 102.0]
        assert fetch.hours[0].pollutants["pm25"] == 61.0

    @respx.mock
    async def test_rejects_response_without_ind_cpcb(self) -> None:
        respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(
                200, json={"hoursInfo": [_hour_info("2024-11-01T00:00:00Z", code="uaqi")]}
            )
        )
        async with GoogleAirQualityClient("key") as client:
            fetch = await client.fetch_history(KOLKATA, cell="h3-1", hours=1)
        assert fetch.hours == ()

    @respx.mock
    async def test_paginates_and_counts_billable_events(self) -> None:
        route = respx.post("https://airquality.googleapis.com/v1/history:lookup")
        route.side_effect = [
            httpx.Response(
                200,
                json={
                    "hoursInfo": [_hour_info("2024-11-01T00:00:00Z")],
                    "nextPageToken": "page-2",
                },
            ),
            httpx.Response(200, json={"hoursInfo": [_hour_info("2024-11-01T01:00:00Z")]}),
        ]
        async with GoogleAirQualityClient("key") as client:
            fetch = await client.fetch_history(KOLKATA, cell="h3-1", hours=2)

        assert fetch.page_count == 2
        assert fetch.billable_events == 2
        assert len(fetch.hours) == 2
        second = json.loads(route.calls[1].request.content)
        assert second["pageToken"] == "page-2"

    @respx.mock
    async def test_http_error_raises(self) -> None:
        respx.post("https://airquality.googleapis.com/v1/history:lookup").mock(
            return_value=httpx.Response(403, text="billing disabled")
        )
        async with GoogleAirQualityClient("key") as client:
            with pytest.raises(AirQualityProviderError, match="403"):
                await client.fetch_history(KOLKATA, cell="h3-1", hours=1)


class TestForecastEndpoint:
    @respx.mock
    async def test_forecast_still_uses_hourly_forecasts_key(self) -> None:
        respx.post("https://airquality.googleapis.com/v1/forecast:lookup").mock(
            return_value=httpx.Response(
                200, json={"hourlyForecasts": [_hour_info("2024-11-01T00:00:00Z", aqi=99.0)]}
            )
        )
        async with GoogleAirQualityClient("key") as client:
            fetch = await client.fetch_forecast(KOLKATA, cell="h3-1", hours=1)
        assert fetch.hours[0].aqi == 99.0

    async def test_forecast_rejects_more_than_96_hours(self) -> None:
        async with GoogleAirQualityClient("key") as client:
            with pytest.raises(ValueError):
                await client.fetch_forecast(KOLKATA, cell="h3-1", hours=FORECAST_MAX_HOURS + 1)


class TestClientBounds:
    def test_rejects_page_size_over_documented_max(self) -> None:
        with pytest.raises(ValueError, match="page_size"):
            GoogleAirQualityClient("key", page_size=HISTORY_MAX_PAGE_SIZE + 1)

    def test_requires_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            GoogleAirQualityClient("")
