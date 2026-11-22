"""Bias-corrected GridProvider anchoring models to ground monitors (Sprint 1 - A2)."""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aeropath.domain.interpolation import haversine_km
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


class StationCorrectedProvider:
    """Wraps an atmospheric grid provider and bias-corrects it against live ground stations."""

    name = "station_corrected_cams"

    def __init__(
        self,
        base_provider: GridProvider,
        *,
        stations_loader: StationsLoader | None = None,
        shrinkage_factor: float = 0.75,
        max_reading_age_h: float = 3.0,
    ) -> None:
        self._base = base_provider
        self._stations_loader = stations_loader
        self._shrinkage = shrinkage_factor
        self._max_age_h = max_reading_age_h

    async def __aenter__(self) -> StationCorrectedProvider:
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
        """Fetch base model concentrations and apply shrunk ground-station bias correction."""
        if not points:
            return []

        # 1. Fetch base model readings
        base_readings = await self._base.concentrations_at(points, at=at)
        if not base_readings or all(not r.concentrations for r in base_readings):
            return base_readings

        # 2. Load ground stations
        raw_stations: list[GroundStationReading] = []
        if self._stations_loader:
            try:
                res = self._stations_loader()
                if inspect.isawaitable(res):
                    raw_stations = await res
                elif isinstance(res, list):
                    raw_stations = res
            except Exception as exc:
                logger.warning("failed to load ground stations: %s", exc)

        ref_time = at or datetime.now(UTC)
        valid_stations = filter_station_readings(
            raw_stations, reference_time=ref_time, max_age_hours=self._max_age_h
        )

        # 3. If no ground stations exist, gracefully fall back to base uncorrected model
        if not valid_stations:
            return base_readings

        # 4. Compute residuals per pollutant between stations and base model
        station_coords = [s.coordinate for s in valid_stations]
        station_base_readings = await self._base.concentrations_at(station_coords, at=at)

        residuals_by_pollutant: dict[str, list[float]] = defaultdict(list)
        for station, st_base in zip(valid_stations, station_base_readings, strict=False):
            base_val = st_base.concentrations.get(station.pollutant)
            if base_val is not None:
                residuals_by_pollutant[station.pollutant].append(station.value - base_val)

        # Compute shrunk residual per pollutant: delta = shrinkage * mean(residuals)
        shrunk_deltas: dict[str, float] = {}
        for pollutant, res_list in residuals_by_pollutant.items():
            if res_list:
                mean_res = sum(res_list) / len(res_list)
                shrunk_deltas[pollutant] = self._shrinkage * mean_res

        # 5. Apply correction to all points
        corrected: list[PointReading] = []
        for point, base_reading in zip(points, base_readings, strict=False):
            if not base_reading.concentrations:
                corrected.append(base_reading)
                continue

            adj_concs: dict[str, float] = {}
            for pollutant, val in base_reading.concentrations.items():
                delta = shrunk_deltas.get(pollutant, 0.0)
                adj_concs[pollutant] = max(0.0, val + delta)

            # Nearest station distance to determine confidence
            min_dist = min(
                (haversine_km(point, s.coordinate) for s in valid_stations),
                default=float("inf"),
            )

            corrected.append(
                PointReading(
                    coordinate=point,
                    concentrations=adj_concs,
                    source=self.name,
                    nearest_station_km=min_dist,
                    observed_at=base_reading.observed_at,
                    is_estimated=False,
                    averaging_hours=base_reading.averaging_hours,
                )
            )

        return corrected
