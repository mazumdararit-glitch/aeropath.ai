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
from datetime import date, datetime

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

BASE_URL = "https://airquality.xkdr.org"
# Public demo key from the dataset's own documentation: reads 2024 bulk files,
# capped at 10k rows per query. A real key removes both limits.
DEMO_API_KEY = "aqi_demo_wbf92Qx21zX-Wa_Tg8Dx1nXe"


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


@dataclass(frozen=True, slots=True)
class Reading:
    station_id: str
    parameter: str
    unit: str
    period_start: datetime
    mean: float
    count: int


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

    async def stations(self, *, city: str) -> list[Station]:
        response = await self._client.get("/v1/stations", params={"city": city})
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
                )
            )
        return out

    async def measurements(
        self,
        *,
        city: str,
        parameters: list[str],
        start: date,
        end: date,
        agg: str = "hourly",
        limit: int | None = None,
    ) -> list[Reading]:
        params: dict[str, object] = {
            "city": city,
            "parameter": parameters,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "agg": agg,
            "format": "json",
        }
        if limit is not None:
            params["limit"] = limit

        response = await self._client.get("/v1/measurements", params=params)
        if response.status_code != httpx.codes.OK:
            raise StationDataError(
                f"measurements failed: {response.status_code} {response.text[:300]}"
            )
        if response.headers.get("X-Truncated") == "true":
            logger.warning("station query truncated at %s rows", response.headers.get("X-Row-Count"))

        payload = response.json()
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
        return out


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
