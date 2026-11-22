"""Spatial IDW interpolation over ground-station pollutant concentrations (Sprint 3 - Task A5).

Performs inverse-distance weighting (IDW) per pollutant across ground stations.
Falls back to nearest-station bias correction when fewer than min_stations (default 3)
are within the search radius.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aeropath.domain.interpolation import haversine_km, inverse_distance_weights
from aeropath.external.bias_corrected_provider import StationCorrectedProvider
from aeropath.external.provider import GridProvider, PointReading
from aeropath.external.station_providers import (
    GroundStationReading,
    filter_station_readings,
)
from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

StationsLoader = Callable[
    [],
    list[GroundStationReading] | Awaitable[list[GroundStationReading]],
]


class StationIDWProvider:
    """Interpolates ground-station pollutant concentrations using Inverse Distance Weighting."""

    name: str = "station_idw"

    def __init__(
        self,
        base_provider: GridProvider,
        *,
        stations_loader: StationsLoader | None = None,
        min_stations: int = 3,
        power: float = 2.0,
        max_distance_km: float = 25.0,
        shrinkage_factor: float = 0.75,
        max_reading_age_h: float = 3.0,
    ) -> None:
        self._base = base_provider
        self._stations_loader = stations_loader
        self._min_stations = min_stations
        self._power = power
        self._max_distance_km = max_distance_km

        self._fallback_provider = StationCorrectedProvider(
            base_provider,
            stations_loader=stations_loader,
            shrinkage_factor=shrinkage_factor,
            max_reading_age_h=max_reading_age_h,
        )

    async def __aenter__(self) -> StationIDWProvider:
        if hasattr(self._base, "__aenter__"):
            await self._base.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if hasattr(self._base, "__aexit__"):
            await self._base.__aexit__(*exc_info)

    async def aclose(self) -> None:
        if hasattr(self._base, "aclose"):
            await self._base.aclose()

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        """Interpolate concentrations using IDW where >= min_stations exist; fallback otherwise."""
        if not points:
            return []

        # 1. Load stations
        raw_stations: list[GroundStationReading] = []
        if self._stations_loader:
            try:
                res = self._stations_loader()
                if inspect.isawaitable(res):
                    raw_stations = await res
                elif isinstance(res, list):
                    raw_stations = res
            except Exception as exc:
                logger.warning("Failed to load ground stations for IDW: %s", exc)

        valid_stations = filter_station_readings(
            raw_stations, max_age_hours=3.0, reference_time=at or datetime.now(UTC)
        )

        # If no stations at all, use base provider
        if not valid_stations:
            return await self._base.concentrations_at(points, at=at)

        # 2. Fetch base model readings (used for fallback or missing pollutants)
        base_readings = await self._base.concentrations_at(points, at=at)

        # 3. For each target point, attempt per-pollutant IDW
        readings: list[PointReading] = []

        # Group stations by pollutant
        stations_by_pollutant: dict[str, list[GroundStationReading]] = defaultdict(list)
        for st in valid_stations:
            stations_by_pollutant[st.pollutant].append(st)

        for pt_idx, pt in enumerate(points):
            base_r = base_readings[pt_idx] if pt_idx < len(base_readings) else None
            interpolated_concs: dict[str, float] = {}
            idw_used_count = 0
            min_dist = float("inf")

            for pollutant, st_list in stations_by_pollutant.items():
                # Find stations within max_distance_km
                nearby = [
                    s for s in st_list if haversine_km(pt, s.coordinate) <= self._max_distance_km
                ]

                if len(nearby) >= self._min_stations:
                    # Perform IDW for this pollutant
                    coords_map = {s.station_id: s.coordinate for s in nearby}
                    weights = inverse_distance_weights(
                        pt, coords_map, power=self._power, max_distance_km=self._max_distance_km
                    )
                    val_map = {s.station_id: s.value for s in nearby}

                    total_w = sum(weights.values())
                    if total_w > 0:
                        weighted_sum = sum(val_map[sid] * w for sid, w in weights.items())
                        interpolated_concs[pollutant] = weighted_sum / total_w
                        idw_used_count += 1
                        for s in nearby:
                            d = haversine_km(pt, s.coordinate)
                            if d < min_dist:
                                min_dist = d

            # If at least one primary particulate pollutant was interpolated via IDW:
            if idw_used_count > 0:
                # Fill in any missing non-interpolated pollutants from base model
                if base_r and base_r.concentrations:
                    for pol, val in base_r.concentrations.items():
                        if pol not in interpolated_concs:
                            interpolated_concs[pol] = val

                readings.append(
                    PointReading(
                        coordinate=pt,
                        concentrations=interpolated_concs,
                        source=self.name,
                        nearest_station_km=min_dist if min_dist != float("inf") else 2.0,
                        observed_at=at or datetime.now(UTC),
                    )
                )
            else:
                # Fewer than min_stations for all pollutants -> fall back to nearest station
                fallback_pts = await self._fallback_provider.concentrations_at([pt], at=at)
                readings.extend(fallback_pts)

        return readings
