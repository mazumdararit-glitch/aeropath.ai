"""CPCB station client: truncation detection and day-chunked fetching.

The endpoint has no working pagination and silently caps results, so these
tests pin the guarantee that matters: a caller either gets every station in the
window, or an error. A quiet subset would corrupt every downstream metric while
looking like success.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from aeropath.external.cpcb_stations import (
    StationClient,
    StationDataError,
    _day_chunks,
)

BASE = "https://airquality.xkdr.org"
MAX_ROWS = 10_000


def _row(station_id: str, hour: str = "2024-11-01T00:00:00") -> dict[str, object]:
    return {
        "station_id": station_id,
        "parameter_name": "PM2.5",
        "unit": "µg/m³",
        "period_start": hour,
        "mean": 60.0,
        "n": 1,
    }


def _ok(rows: int, *, truncated: bool = False) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "meta": {"rows": rows, "truncated": truncated},
            "data": [_row(f"site_{i}") for i in range(rows)],
        },
    )


class TestDayChunks:
    def test_single_day(self) -> None:
        assert _day_chunks(date(2024, 11, 1), date(2024, 11, 1)) == [
            (date(2024, 11, 1), date(2024, 11, 1))
        ]

    def test_multi_day_is_contiguous_and_complete(self) -> None:
        chunks = _day_chunks(date(2024, 11, 1), date(2024, 11, 5))
        assert len(chunks) == 5
        assert chunks[0][0] == date(2024, 11, 1)
        assert chunks[-1][1] == date(2024, 11, 5)
        for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:], strict=False):
            assert (next_start - prev_end).days == 1

    def test_rejects_inverted_range(self) -> None:
        with pytest.raises(ValueError, match="precedes"):
            _day_chunks(date(2024, 11, 5), date(2024, 11, 1))


class TestMeasurements:
    @respx.mock
    async def test_splits_into_one_request_per_day(self) -> None:
        route = respx.get(f"{BASE}/v1/measurements").mock(return_value=_ok(10))
        async with StationClient() as client:
            readings = await client.measurements(
                city="Delhi",
                parameters=["PM2.5"],
                start=date(2024, 11, 1),
                end=date(2024, 11, 3),
            )
        assert route.call_count == 3
        assert len(readings) == 30

    @respx.mock
    async def test_does_not_warn_or_drop_when_untruncated(self) -> None:
        respx.get(f"{BASE}/v1/measurements").mock(return_value=_ok(39))
        async with StationClient() as client:
            readings = await client.measurements(
                city="Delhi", parameters=["PM2.5"], start=date(2024, 11, 1), end=date(2024, 11, 1)
            )
        assert len({r.station_id for r in readings}) == 39

    @respx.mock
    async def test_truncated_day_splits_parameters_rather_than_returning_a_subset(self) -> None:
        """The regression: a capped day must not masquerade as a full result.

        Days are already single days here, so the only axis left is the parameter
        list; it is halved until each request fits under the cap.
        """

        def responder(request: httpx.Request) -> httpx.Response:
            count = len(request.url.params.get_list("parameter"))
            if count == 1:
                return _ok(5)
            return _ok(MAX_ROWS, truncated=True)

        route = respx.get(f"{BASE}/v1/measurements").mock(side_effect=responder)
        async with StationClient() as client:
            readings = await client.measurements(
                city="Delhi",
                parameters=["PM2.5", "PM10"],
                start=date(2024, 11, 1),
                end=date(2024, 11, 1),
            )

        # One truncated attempt, then one request per single parameter.
        assert route.call_count == 3
        assert len(readings) == 10

    @respx.mock
    async def test_truncated_single_day_raises_instead_of_losing_stations(self) -> None:
        respx.get(f"{BASE}/v1/measurements").mock(return_value=_ok(MAX_ROWS, truncated=True))
        async with StationClient() as client:
            with pytest.raises(StationDataError, match="single parameter"):
                await client.measurements(
                    city="Delhi",
                    parameters=["PM2.5"],
                    start=date(2024, 11, 1),
                    end=date(2024, 11, 1),
                )

    @respx.mock
    async def test_falls_back_to_headers_when_body_meta_absent(self) -> None:
        respx.get(f"{BASE}/v1/measurements").mock(
            return_value=httpx.Response(
                200,
                headers={"X-Truncated": "true", "X-Row-Count": str(MAX_ROWS)},
                json={"data": [_row("site_1")]},
            )
        )
        async with StationClient() as client:
            with pytest.raises(StationDataError, match="truncated"):
                await client.measurements(
                    city="Delhi",
                    parameters=["PM2.5"],
                    start=date(2024, 11, 1),
                    end=date(2024, 11, 1),
                )

    @respx.mock
    async def test_sends_city_or_state_filter(self) -> None:
        route = respx.get(f"{BASE}/v1/measurements").mock(return_value=_ok(1))
        async with StationClient() as client:
            await client.measurements(
                state="Delhi",
                parameters=["PM2.5"],
                start=date(2024, 11, 1),
                end=date(2024, 11, 1),
            )
        assert route.calls[0].request.url.params["state"] == "Delhi"
        assert "city" not in route.calls[0].request.url.params


class TestStations:
    @respx.mock
    async def test_nationwide_enumeration_needs_no_filter(self) -> None:
        route = respx.get(f"{BASE}/v1/stations").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "station_id": "site_1",
                            "station_name": "A",
                            "latitude": 28.6,
                            "longitude": 77.2,
                            "source": "cpcb_caaqm",
                            "city_name": "Delhi",
                            "state_name": "Delhi",
                        }
                    ]
                },
            )
        )
        async with StationClient() as client:
            stations = await client.stations()

        assert "city" not in route.calls[0].request.url.params
        assert stations[0].city == "Delhi"
        assert stations[0].state == "Delhi"

    @respx.mock
    async def test_city_filter_is_passed_through(self) -> None:
        route = respx.get(f"{BASE}/v1/stations").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        async with StationClient() as client:
            await client.stations(city="Delhi")
        assert route.calls[0].request.url.params["city"] == "Delhi"
