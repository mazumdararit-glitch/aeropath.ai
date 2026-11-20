"""Google Air Quality API client.

Two things this client does that a naive wrapper would not:

* **Pins the index to ``ind_cpcb``.** The API returns *Universal* AQI by default,
  which is a different scale with different breakpoints. Requesting the local
  index requires an explicit ``customLocalAqis`` entry and a response check, so
  a silently-changed default can never repaint the map.
* **Counts billable events.** Phase 0 exists partly to find out how many calls a
  96-hour window actually costs, so every response records what it was charged
  for. The guess in the plan ("pageSize defaults to 24") is exactly the sort of
  assumption this harness is meant to replace with a measurement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

BASE_URL = "https://airquality.googleapis.com"
INDEX_CODE = "ind_cpcb"


class AirQualityProviderError(RuntimeError):
    """The provider failed or returned something we refuse to interpret."""


@dataclass(frozen=True, slots=True)
class HourlyCell:
    cell: str
    hour: datetime
    aqi: float
    category: str
    dominant_pollutant: str | None
    pollutants: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderFetch:
    """One logical request, with the measurements Phase 0 needs."""

    cell: str
    hours: tuple[HourlyCell, ...]
    page_count: int
    requests_made: int
    truncated: bool

    @property
    def billable_events(self) -> int:
        return self.requests_made


class GoogleAirQualityClient:
    """Minimal async client. Retries live in the shared ``ResilientClient`` later;
    Phase 0 calls synchronously enough that a timeout is sufficient here."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = 20.0,
        page_size: int = 96,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._page_size = page_size
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout_s,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    async def __aenter__(self) -> GoogleAirQualityClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_forecast(
        self,
        coordinate: Coordinate,
        *,
        cell: str,
        hours: int = 96,
        start: datetime | None = None,
    ) -> ProviderFetch:
        """Fetch an hourly forecast window for one point.

        Paginates because the documented default page size is 24 and Phase 0
        must establish the real cap rather than assume it.
        """
        if not 1 <= hours <= 96:
            raise ValueError("hours must be 1-96")

        start = start or datetime.now(tz=None).astimezone()
        body: dict[str, object] = {
            "location": {"latitude": coordinate.lat, "longitude": coordinate.lng},
            "period": {
                "startTime": start.isoformat(),
                "endTime": (start + timedelta(hours=hours - 1)).isoformat(),
            },
            "pageSize": self._page_size,
            "extraComputations": ["LOCAL_AQI", "DOMINANT_POLLUTANT_CONCENTRATION"],
            "customLocalAqis": [{"regionCode": "IN", "aqi": INDEX_CODE}],
            "languageCode": "en",
        }

        collected: list[HourlyCell] = []
        page_count = 0
        token: str | None = None

        while True:
            payload = dict(body)
            if token:
                payload["pageToken"] = token
            response = await self._client.post("/v1/forecast:lookup", params={"key": self._api_key}, json=payload)
            page_count += 1

            if response.status_code != httpx.codes.OK:
                raise AirQualityProviderError(
                    f"forecast lookup failed: {response.status_code} {response.text[:400]}"
                )

            data = response.json()
            collected.extend(_parse_hourly_forecasts(data, cell=cell))

            token = data.get("nextPageToken")
            if not token:
                break
            if page_count > 10:
                raise AirQualityProviderError("forecast pagination did not terminate")

        return ProviderFetch(
            cell=cell,
            hours=tuple(collected),
            page_count=page_count,
            requests_made=page_count,
            truncated=False,
        )

    async def fetch_current(self, coordinate: Coordinate, *, cell: str) -> HourlyCell:
        body = {
            "location": {"latitude": coordinate.lat, "longitude": coordinate.lng},
            "extraComputations": ["LOCAL_AQI", "DOMINANT_POLLUTANT_CONCENTRATION"],
            "customLocalAqis": [{"regionCode": "IN", "aqi": INDEX_CODE}],
            "languageCode": "en",
        }
        response = await self._client.post(
            "/v1/currentConditions:lookup", params={"key": self._api_key}, json=body
        )
        if response.status_code != httpx.codes.OK:
            raise AirQualityProviderError(
                f"current conditions failed: {response.status_code} {response.text[:400]}"
            )
        data = response.json()
        parsed = _parse_hourly_forecasts({"hourlyForecasts": [data]}, cell=cell)
        if not parsed:
            raise AirQualityProviderError("current conditions returned no ind_cpcb index")
        return parsed[0]


def _parse_hourly_forecasts(data: dict[str, object], *, cell: str) -> list[HourlyCell]:
    raw = data.get("hourlyForecasts")
    if not isinstance(raw, list):
        raise AirQualityProviderError("response missing hourlyForecasts list")

    out: list[HourlyCell] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        index = _pick_ind_cpcb(entry)
        if index is None:
            # A cell with no Indian index is a coverage gap, not a crash. Phase 0
            # counts these; production lowers confidence.
            logger.debug("no %s index in entry for cell %s", INDEX_CODE, cell)
            continue

        pollutants = {
            p["code"]: float(p["concentration"]["value"])
            for p in entry.get("pollutants", [])
            if isinstance(p, dict) and "code" in p and "concentration" in p
        }
        out.append(
            HourlyCell(
                cell=cell,
                hour=datetime.fromisoformat(str(entry["dateTime"]).replace("Z", "+00:00")),
                aqi=float(index["aqi"]),
                category=str(index.get("category", "")),
                dominant_pollutant=index.get("dominantPollutant"),
                pollutants=pollutants,
            )
        )
    return out


def _pick_ind_cpcb(entry: dict[str, object]) -> dict[str, object] | None:
    indexes = entry.get("indexes")
    if not isinstance(indexes, list):
        return None
    for index in indexes:
        if isinstance(index, dict) and index.get("code") == INDEX_CODE:
            return index
    return None
