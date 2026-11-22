"""Ground station providers: WAQI and OpenAQ v3 clients with quality checks (Sprint 1 - A2)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

# Valid physical concentration ranges for quality checks (µg/m³; CO in mg/m³)
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "PM2.5": (0.0, 1000.0),
    "PM10": (0.0, 1500.0),
    "NO2": (0.0, 1000.0),
    "SO2": (0.0, 1000.0),
    "Ozone": (0.0, 800.0),
    "CO": (0.0, 100.0),
}


@dataclass(frozen=True, slots=True)
class GroundStationReading:
    """A single verified measurement from an official ground monitor."""

    station_id: str
    name: str
    coordinate: Coordinate
    pollutant: str
    value: float
    unit: str
    timestamp: datetime
    source: str
    attribution: str


def filter_station_readings(
    readings: Sequence[GroundStationReading],
    *,
    reference_time: datetime | None = None,
    max_age_hours: float = 3.0,
) -> list[GroundStationReading]:
    """Filter out stale readings, negatives, impossible values, and extreme outliers."""
    now = reference_time or datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)

    valid: list[GroundStationReading] = []
    for r in readings:
        # Check freshness
        if r.timestamp < cutoff or r.timestamp > now + timedelta(minutes=15):
            continue

        # Check plausible physical range
        bounds = PLAUSIBLE_RANGES.get(r.pollutant)
        if bounds:
            low, high = bounds
            if not (low <= r.value <= high):
                continue
        elif r.value < 0:
            continue

        valid.append(r)

    return valid


class WaqiClient:
    """World Air Quality Index (WAQI) real-time API client."""

    BASE_URL = "https://api.waqi.info"

    def __init__(self, token: str = "", *, timeout_s: float = 15.0) -> None:
        self._token = token
        self._timeout_s = timeout_s

    async def fetch_in_bbox(
        self,
        min_lat: float,
        min_lng: float,
        max_lat: float,
        max_lng: float,
    ) -> list[GroundStationReading]:
        """Fetch all ground stations within a bounding box."""
        if not self._token:
            logger.debug("WAQI_TOKEN is not configured; skipping WAQI fetch")
            return []

        url = f"{self.BASE_URL}/v2/map/bounds"
        params = {
            "latlng": f"{min_lat},{min_lng},{max_lat},{max_lng}",
            "token": self._token,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("WAQI bounds request returned status %s", resp.status_code)
                    return []
                payload = resp.json()
        except Exception as exc:
            logger.warning("WAQI fetch failed: %s", exc)
            return []

        if payload.get("status") != "ok":
            return []

        readings: list[GroundStationReading] = []
        for item in payload.get("data") or []:
            lat = item.get("lat")
            lon = item.get("lon")
            if lat is None or lon is None:
                continue

            station_info = item.get("station") or {}
            time_str = station_info.get("time")
            try:
                if time_str:
                    ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                else:
                    ts = datetime.now(UTC)
            except Exception:
                ts = datetime.now(UTC)

            # WAQI map bounds returns aqi value (predominantly PM2.5 in Indian cities)
            aqi_val = item.get("aqi")
            try:
                val = float(aqi_val)
            except (ValueError, TypeError):
                continue

            readings.append(
                GroundStationReading(
                    station_id=f"waqi_{item.get('uid', 'unknown')}",
                    name=station_info.get("name", "WAQI Station"),
                    coordinate=Coordinate(lat=float(lat), lng=float(lon)),
                    pollutant="PM2.5",
                    value=val,
                    unit="µg/m³",
                    timestamp=ts,
                    source="waqi",
                    attribution="World Air Quality Index project (WAQI) & CPCB",
                )
            )

        return readings


class OpenAQClient:
    """OpenAQ v3 API client for official global air quality ground data."""

    BASE_URL = "https://api.openaq.org/v3"

    def __init__(self, api_key: str = "", *, timeout_s: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def fetch_in_bbox(
        self,
        min_lat: float,
        min_lng: float,
        max_lat: float,
        max_lng: float,
    ) -> list[GroundStationReading]:
        """Fetch ground monitor readings within a bounding box."""
        if not self._api_key:
            logger.debug("OPENAQ_API_KEY is not configured; skipping OpenAQ fetch")
            return []

        headers = {"X-API-Key": self._api_key}
        url = f"{self.BASE_URL}/locations"
        params = {
            "bbox": f"{min_lng},{min_lat},{max_lng},{max_lat}",
            "limit": "50",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, headers=headers) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("OpenAQ locations returned %s", resp.status_code)
                    return []
                payload = resp.json()
        except Exception as exc:
            logger.warning("OpenAQ request failed: %s", exc)
            return []

        readings: list[GroundStationReading] = []
        for loc in payload.get("results") or []:
            coords = loc.get("coordinates") or {}
            lat = coords.get("latitude")
            lng = coords.get("longitude")
            if lat is None or lng is None:
                continue

            # Sensors in location
            for sensor in loc.get("sensors") or []:
                param = sensor.get("parameter", {}).get("name")
                summary = sensor.get("summary") or {}
                val = summary.get("value")
                if val is not None and param:
                    readings.append(
                        GroundStationReading(
                            station_id=f"openaq_{loc.get('id', 'unk')}",
                            name=loc.get("name", "OpenAQ Station"),
                            coordinate=Coordinate(lat=float(lat), lng=float(lng)),
                            pollutant=param.upper(),
                            value=float(val),
                            unit="µg/m³",
                            timestamp=datetime.now(UTC),
                            source="openaq",
                            attribution="OpenAQ Community & Official Partners",
                        )
                    )

        return readings
