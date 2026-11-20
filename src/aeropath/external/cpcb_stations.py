"""CPCB station readings, via the XKDR Forum India Air Quality Database.

Why not the CPCB portal directly: ``airquality.cpcb.gov.in`` is slow and
frequently times out, and it publishes HTML. XKDR republishes the *same* CPCB
CAAQM network as a queryable API and bulk Parquet, with readings back to 2009,
which is what a validation harness actually needs.

Data caveats that must not be silently dropped (see the dataset's own notes):
values are as received from the networks, with no gap filling or calibration, and
readings in Kolkata are sparse relative to the city's area. Both matter for the
confidence rule, so both are surfaced rather than smoothed over.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

BASE_URL = "https://airquality.xkdr.org"
# Public demo key from the dataset's own documentation: reads 2024 bulk files,
# capped at 10k rows per query. A real key removes both limits.
DEMO_API_KEY = "aqi_demo_wbf92Qx21zX-Wa_Tg8Dx1nXe"

# The dataset holds 558 stations nationwide; ask for enough headroom that a
# growth spurt does not silently truncate the enumeration.
STATION_PAGE_LIMIT = 100_000


class StationDataError(RuntimeError):
    """Station data is unavailable or malformed."""


@dataclass(frozen=True, slots=True)
class Station:
    station_id: str
    name: str
    coordinate: Coordinate
    source: str
    parameters: tuple[str, ...]
    n_rows: int
    city: str | None = None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class Reading:
    station_id: str
    parameter: str
    unit: str
    period_start: datetime
    mean: float
    count: int


@dataclass(frozen=True, slots=True)
class QueryMeta:
    """What the server says it did with a query.

    ``truncated`` is the important one. The endpoint silently caps a result set
    at ``max_rows`` and the cap is applied *after* ordering, so a large query
    returns a valid-looking subset of stations rather than an error. A harness
    that ignores this reports metrics over whichever stations happened to sort
    first, which is worse than failing outright.
    """

    rows: int
    truncated: bool
    max_rows: int

    @property
    def complete(self) -> bool:
        return not self.truncated


class StationClient:
    def __init__(
        self,
        api_key: str = DEMO_API_KEY,
        *,
        timeout_s: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def __aenter__(self) -> StationClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stations(self, *, city: str | None = None, state: str | None = None) -> list[Station]:
        """Stations for a city, a state, or the whole country.

        With neither filter this returns every station in the dataset, which is
        the only way to enumerate cities: there is no ``/v1/cities`` endpoint.
        """
        params: dict[str, object] = {"limit": STATION_PAGE_LIMIT}
        if city:
            params["city"] = city
        if state:
            params["state"] = state

        response = await self._client.get("/v1/stations", params=params)
        if response.status_code != httpx.codes.OK:
            raise StationDataError(f"stations failed: {response.status_code}")
        payload = response.json()
        out: list[Station] = []
        for row in payload.get("data", []):
            if row.get("latitude") is None or row.get("longitude") is None:
                continue
            out.append(
                Station(
                    station_id=str(row["station_id"]),
                    name=str(row.get("station_name") or row["station_id"]),
                    coordinate=Coordinate(lat=float(row["latitude"]), lng=float(row["longitude"])),
                    source=str(row.get("source", "unknown")),
                    parameters=tuple(row.get("parameters") or ()),
                    n_rows=int(row.get("n_rows") or 0),
                    city=str(row["city_name"]) if row.get("city_name") else None,
                    state=str(row["state_name"]) if row.get("state_name") else None,
                )
            )
        return out

    async def measurements(
        self,
        *,
        city: str | None = None,
        state: str | None = None,
        parameters: list[str],
        start: date,
        end: date,
        agg: str = "hourly",
        limit: int | None = None,
    ) -> list[Reading]:
        """All readings in the window, guaranteed complete or an error.

        The endpoint has no working pagination (``offset`` is accepted and
        ignored) and silently caps results at 10k rows *by station*, so a naive
        range query on a dense network returns 15 of Delhi's 39 stations while
        looking entirely successful. Two axes are therefore subdivided: the
        window by day, and — if a single day still exceeds the cap — the
        parameter list. The failure mode is a loud error rather than a quiet
        subset.
        """
        readings: list[Reading] = []
        for chunk_start, chunk_end in _day_chunks(start, end):
            readings.extend(
                await self._measurements_exact(
                    city=city,
                    state=state,
                    parameters=parameters,
                    start=chunk_start,
                    end=chunk_end,
                    agg=agg,
                    limit=limit,
                )
            )
        return readings

    async def _measurements_exact(
        self,
        *,
        city: str | None,
        state: str | None,
        parameters: list[str],
        start: date,
        end: date,
        agg: str,
        limit: int | None,
    ) -> list[Reading]:
        """Fetch one day, halving the parameter list if the server truncated it.

        Rows scale with the number of parameters requested, so parameters are the
        axis that can be subdivided once a single day is already too big. Splitting
        by date instead would be dead code here, because the caller has already
        reduced the window to one day.
        """
        readings, meta = await self._measurements_once(
            city=city,
            state=state,
            parameters=parameters,
            start=start,
            end=end,
            agg=agg,
            limit=limit,
        )
        if meta.complete:
            return readings

        if len(parameters) == 1:
            raise StationDataError(
                f"station query truncated even for {start} with a single "
                f"parameter ({parameters[0]}): {meta.rows} of max {meta.max_rows} "
                "rows returned. A CPCB index needs several pollutants at once, so "
                "this window cannot be validated. Narrow the date range or pass a "
                "real API key."
            )

        midpoint = len(parameters) // 2
        left_params = parameters[:midpoint]
        right_params = parameters[midpoint:]
        logger.warning(
            "station query truncated (%s rows, max %s) for %s; splitting %s into %s and %s",
            meta.rows,
            meta.max_rows,
            start,
            parameters,
            left_params,
            right_params,
        )
        left = await self._measurements_exact(
            city=city,
            state=state,
            parameters=left_params,
            start=start,
            end=end,
            agg=agg,
            limit=limit,
        )
        right = await self._measurements_exact(
            city=city,
            state=state,
            parameters=right_params,
            start=start,
            end=end,
            agg=agg,
            limit=limit,
        )
        return left + right

    async def _measurements_once(
        self,
        *,
        city: str | None,
        state: str | None,
        parameters: list[str],
        start: date,
        end: date,
        agg: str,
        limit: int | None,
    ) -> tuple[list[Reading], QueryMeta]:
        params: dict[str, object] = {
            "parameter": parameters,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "agg": agg,
            "format": "json",
        }
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if limit is not None:
            params["limit"] = limit

        response = await self._client.get("/v1/measurements", params=params)
        if response.status_code != httpx.codes.OK:
            raise StationDataError(
                f"measurements failed: {response.status_code} {response.text[:300]}"
            )

        payload = response.json()
        meta = _parse_meta(payload, response)
        out: list[Reading] = []
        for row in payload.get("data", []):
            if row.get("mean") is None:
                continue
            out.append(
                Reading(
                    station_id=str(row["station_id"]),
                    parameter=str(row["parameter_name"]),
                    unit=str(row.get("unit", "")),
                    period_start=datetime.fromisoformat(str(row["period_start"])),
                    mean=float(row["mean"]),
                    count=int(row.get("n") or 0),
                )
            )
        return out, meta


def _parse_meta(payload: dict[str, object], response: httpx.Response) -> QueryMeta:
    """Read truncation state from the body, falling back to headers.

    Trusting the body avoids depending on ``X-Truncated``, which is an
    undocumented header: if the server stops sending it, the body still carries
    the same information.
    """
    body_meta = payload.get("meta")
    if isinstance(body_meta, dict):
        return QueryMeta(
            rows=int(body_meta.get("rows") or 0),
            truncated=bool(body_meta.get("truncated") or False),
            max_rows=int(body_meta.get("max_rows") or 0),
        )
    header_truncated = response.headers.get("X-Truncated") == "true"
    return QueryMeta(
        rows=int(response.headers.get("X-Row-Count") or 0),
        truncated=header_truncated,
        max_rows=int(response.headers.get("X-Max-Rows") or 0),
    )


def _day_chunks(start: date, end: date) -> list[tuple[date, date]]:
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")
    chunks: list[tuple[date, date]] = []
    current = start
    while current <= end:
        chunks.append((current, current))
        current += timedelta(days=1)
    return chunks


def pivot_readings(readings: list[Reading]) -> dict[tuple[str, datetime], dict[str, float]]:
    """Group flat readings into ``(station, hour) -> {parameter: value}``.

    The CPCB index needs several pollutants simultaneously, so the flat time
    series has to be pivoted before any AQI can be computed.
    """
    grouped: dict[tuple[str, datetime], dict[str, float]] = {}
    for reading in readings:
        key = (reading.station_id, reading.period_start)
        grouped.setdefault(key, {})[reading.parameter] = reading.mean
    return grouped
