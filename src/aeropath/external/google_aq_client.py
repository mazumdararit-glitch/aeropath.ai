"""Google Air Quality API client.

Three things this client does that a naive wrapper would not:

* **Pins the index to ``ind_cpcb``.** The API returns *Universal* AQI by default,
  which is a different scale with different breakpoints. Requesting the local
  index requires an explicit ``customLocalAqis`` entry and a response check, so
  a silently-changed default can never repaint the map.
* **Uses ``history:lookup`` for validation, not ``forecast:lookup``.** Phase 0
  compares provider values against station readings, and readings only exist for
  the past. Forecast data would be compared against a different hour and would
  look accurate or inaccurate for reasons that have nothing to do with the model.
* **Counts billable events.** Phase 0 exists partly to find out how many calls a
  96-hour window actually costs, so every response records what it was charged
  for. The guess in the plan ("pageSize defaults to 24") is exactly the sort of
  assumption this harness is meant to replace with a measurement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

BASE_URL = "https://airquality.googleapis.com"
INDEX_CODE = "ind_cpcb"

# Documented by the API reference: history page size defaults to 72 and caps at
# 168 (7 days); the hours window caps at 720 (30 days). Asking for more than the
# documented maximum is a rejection, not a clamp, so these bounds are enforced
# before the request rather than discovered from a 400.
HISTORY_MAX_PAGE_SIZE = 168
HISTORY_MAX_HOURS = 720
FORECAST_MAX_HOURS = 96

# A 720-hour window at 168 per page is 5 pages; the cap only exists to turn a
# server that never stops returning a token into an error.
MAX_PAGES = 20


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
        page_size: int = HISTORY_MAX_PAGE_SIZE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not 1 <= page_size <= HISTORY_MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be 1-{HISTORY_MAX_PAGE_SIZE}")
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
        hours: int = FORECAST_MAX_HOURS,
        start: datetime | None = None,
    ) -> ProviderFetch:
        """Fetch an hourly forecast window for one point.

        Paginates because the documented default page size is 24 and Phase 0
        must establish the real cap rather than assume it.
        """
        if not 1 <= hours <= FORECAST_MAX_HOURS:
            raise ValueError(f"hours must be 1-{FORECAST_MAX_HOURS}")

        start = start or datetime.now(tz=UTC)
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

        return await self._paginate(
            "/v1/forecast:lookup", body, cell=cell, list_key="hourlyForecasts"
        )

    async def fetch_history(
        self,
        coordinate: Coordinate,
        *,
        cell: str,
        hours: int = 72,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ProviderFetch:
        """Fetch measured hours, which is what station readings can be compared to.

        Pass either ``hours`` (a lookback window ending now) or an explicit
        ``start``/``end`` pair. The API accepts one or the other, never both, so
        this never sends both.
        """
        if start is not None or end is not None:
            if start is None or end is None:
                raise ValueError("start and end must be given together")
            if end <= start:
                raise ValueError("end must be after start")
            window_hours = int((end - start).total_seconds() // 3600)
            if window_hours > HISTORY_MAX_HOURS:
                raise ValueError(
                    f"history window is {window_hours}h; the API caps at "
                    f"{HISTORY_MAX_HOURS}h (30 days)"
                )
        else:
            if not 1 <= hours <= HISTORY_MAX_HOURS:
                raise ValueError(f"hours must be 1-{HISTORY_MAX_HOURS}")

        body: dict[str, object] = {
            "location": {"latitude": coordinate.lat, "longitude": coordinate.lng},
            "pageSize": self._page_size,
            "extraComputations": ["LOCAL_AQI", "DOMINANT_POLLUTANT_CONCENTRATION"],
            "customLocalAqis": [{"regionCode": "IN", "aqi": INDEX_CODE}],
            # Without this the response still carries Universal AQI, which is a
            # different scale; we ask for the local index only.
            "universalAqi": False,
            "languageCode": "en",
        }
        if start is not None and end is not None:
            body["period"] = {
                "startTime": start.isoformat(),
                "endTime": end.isoformat(),
            }
        else:
            body["hours"] = hours

        return await self._paginate(
            "/v1/history:lookup", body, cell=cell, list_key="hoursInfo"
        )

    async def _paginate(
        self, path: str, body: dict[str, object], *, cell: str, list_key: str
    ) -> ProviderFetch:
        collected: list[HourlyCell] = []
        page_count = 0
        token: str | None = None

        while True:
            payload = dict(body)
            if token:
                payload["pageToken"] = token
            response = await self._client.post(
                path, params={"key": self._api_key}, json=payload
            )
            page_count += 1

            if response.status_code != httpx.codes.OK:
                raise AirQualityProviderError(
                    f"{path} failed: {response.status_code} {response.text[:400]}"
                )

            data = response.json()
            collected.extend(
                _parse_hourly_entries(data, cell=cell, list_key=list_key)
            )

            token = data.get("nextPageToken")
            if not token:
                break
            if page_count > MAX_PAGES:
                raise AirQualityProviderError(f"{path} pagination did not terminate")

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
        # currentConditions returns a single HourInfo object, not a list, so it is
        # wrapped to reuse the same parser.
        parsed = _parse_hourly_entries(
            {"hourlyForecasts": [data]}, cell=cell, list_key="hourlyForecasts"
        )
        if not parsed:
            raise AirQualityProviderError("current conditions returned no ind_cpcb index")
        return parsed[0]


def _parse_hourly_entries(
    data: dict[str, object], *, cell: str, list_key: str
) -> list[HourlyCell]:
    """Parse an entry list, keyed by whichever field this endpoint uses.

    ``forecast`` and ``history`` name the list differently (``hourlyForecasts``
    vs ``hoursInfo``) but each element has the same shape.
    """
    raw = data.get(list_key)
    if not isinstance(raw, list):
        raise AirQualityProviderError(f"response missing {list_key} list")

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
        dominant = (
            str(index["dominantPollutant"]) if index.get("dominantPollutant") else None
        )
        out.append(
            HourlyCell(
                cell=cell,
                hour=datetime.fromisoformat(str(entry["dateTime"]).replace("Z", "+00:00")),
                aqi=float(str(index["aqi"])),
                category=str(index.get("category", "")),
                dominant_pollutant=dominant,
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
