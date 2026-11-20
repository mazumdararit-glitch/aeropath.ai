"""Live air quality from Open-Meteo's CAMS feed.

Why this exists alongside the XKDR provider: XKDR carries real CPCB station
measurements, but its *public* dataset collapsed after 2024-12 — from ~4.1M
rows/month to ~1.4k, covering two stations nationwide. It is a historical archive
now, not a live source. Open-Meteo carries a live CAMS model with all six
pollutants the CPCB index needs.

The trade-off is spatial. CAMS global for India is a ~45 km grid, so every point
inside a city returns the same values; this cannot rank routes by street. It
*can* answer "how is the air today", which is what a live product needs. The
two providers are deliberately kept behind ``GridProvider`` so the product can
use live CAMS now and swap back to measurements when a live feed exists.

CO is the trap: Open-Meteo reports µg/m³ and the CPCB anchors are mg/m³.
``domain.units`` converts explicitly, which is what stops a normal 1438 µg/m³
reading from saturating the sub-index at 500.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from aeropath.domain.units import normalise
from aeropath.external.provider import PointReading
from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

BASE_URL = "https://air-quality-api.open-meteo.com"

# The six pollutants Open-Meteo exposes that the CPCB index understands. NH3 is
# omitted: it is Europe-only, so requesting it in India returns nulls.
VARIABLES: dict[str, str] = {
    "pm2_5": "PM2.5",
    "pm10": "PM10",
    "nitrogen_dioxide": "NO2",
    "sulphur_dioxide": "SO2",
    "carbon_monoxide": "CO",
    "ozone": "Ozone",
}

# Open-Meteo reports every gas in µg/m³, including CO. The CPCB CO scale is in
# mg/m³, so normalise() must convert rather than pass through.
UNITS: dict[str, str] = {pollutant: "µg/m³" for pollutant in VARIABLES.values()}


@dataclass(frozen=True, slots=True)
class CamsGridInfo:
    """Where the model actually answered from.

    Open-Meteo snaps to a grid cell and returns its centre, which is usually a
    few kilometres from the request. Reporting the distance keeps the product
    honest about resolution instead of implying street-level knowledge.
    """

    cell_lat: float
    cell_lng: float
    offset_km: float


class OpenMeteoProvider:
    """Live concentrations from CAMS, at model resolution (~45 km in India)."""

    name = "open_meteo_cams"

    def __init__(
        self,
        *,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL, timeout=timeout_s, transport=transport
        )
        # Last grid offset seen, so a caller can report model resolution.
        self.last_grid: CamsGridInfo | None = None

    async def __aenter__(self) -> OpenMeteoProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        """Current concentrations for each point.

        ``at`` is accepted for interface parity but ignored: the free tier serves
        current conditions and a forecast, not arbitrary history. Asking for a
        past timestamp would silently return present-day air, so it is not
        pretended otherwise.
        """
        if not points:
            return []
        if at is not None:
            logger.debug("Open-Meteo serves current conditions; ignoring at=%s", at)

        # One request handles many coordinates, which matters because every point
        # in a city lands on the same grid cell anyway.
        latitudes = ",".join(f"{point.lat:.6f}" for point in points)
        longitudes = ",".join(f"{point.lng:.6f}" for point in points)
        params = {
            "latitude": latitudes,
            "longitude": longitudes,
            "current": ",".join(VARIABLES),
            "timezone": "UTC",
        }

        try:
            response = await self._client.get("/v1/air-quality", params=params)
        except httpx.HTTPError as exc:
            logger.warning("open-meteo request failed: %s", exc)
            return [self._empty(point) for point in points]

        if response.status_code != httpx.codes.OK:
            logger.warning(
                "open-meteo returned %s: %s", response.status_code, response.text[:200]
            )
            return [self._empty(point) for point in points]

        payload = response.json()
        # A single coordinate returns an object; several return a list.
        entries = payload if isinstance(payload, list) else [payload]
        if len(entries) != len(points):
            logger.warning(
                "open-meteo returned %d entries for %d points", len(entries), len(points)
            )

        out: list[PointReading] = []
        for index, point in enumerate(points):
            entry = entries[index] if index < len(entries) else None
            if not isinstance(entry, dict):
                out.append(self._empty(point))
                continue

            current = entry.get("current") or {}
            raw: dict[str, float] = {}
            for variable, pollutant in VARIABLES.items():
                value = current.get(variable)
                if value is not None:
                    raw[pollutant] = float(value)

            concentrations = normalise(raw, UNITS)

            cell_lat = float(entry.get("latitude", point.lat))
            cell_lng = float(entry.get("longitude", point.lng))
            offset = _haversine_km(point.lat, point.lng, cell_lat, cell_lng)
            if index == 0:
                self.last_grid = CamsGridInfo(
                    cell_lat=cell_lat, cell_lng=cell_lng, offset_km=offset
                )

            out.append(
                PointReading(
                    coordinate=point,
                    concentrations=concentrations,
                    source=self.name,
                    # The model cell *is* the resolution. Reporting the offset
                    # rather than a station distance keeps the confidence logic
                    # meaningful: a 45 km cell is low confidence for a street.
                    nearest_station_km=offset,
                    observed_at=_parse_time(current.get("time")),
                )
            )
        return out

    def _empty(self, point: Coordinate) -> PointReading:
        return PointReading(
            coordinate=point,
            concentrations={},
            source=self.name,
            nearest_station_km=float("inf"),
        )

    @property
    def resolution_km(self) -> float | None:
        """Observed model resolution, from the last response's grid offset."""
        return self.last_grid.offset_km if self.last_grid else None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC)
    except ValueError:
        return None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    rlat1, rlng1, rlat2, rlng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat, dlng = rlat2 - rlat1, rlng2 - rlng1
    h = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlng / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(h))
