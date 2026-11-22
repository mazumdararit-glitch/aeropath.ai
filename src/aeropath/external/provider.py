"""The grid-data seam.

Everything above this protocol is independent of where concentrations come from,
which is what makes a paid provider replaceable by a free one without touching
the scoring engine, the API, or the planner.

Implementations must return **concentrations**, not an index. The CPCB index is
computed locally from concentrations precisely so a provider cannot silently
substitute a different scale — the failure mode that made the original Google
client pin ``ind_cpcb``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from aeropath.schemas.common import Confidence, Coordinate


@dataclass(frozen=True, slots=True)
class PointReading:
    """Concentrations at one point, plus the evidence behind them."""

    coordinate: Coordinate
    concentrations: dict[str, float]
    source: str
    nearest_station_km: float
    observed_at: datetime | None = None
    is_estimated: bool = False
    averaging_hours: dict[str, int | str] | None = None

    @property
    def confidence(self) -> Confidence:
        """How much to trust this point.

        ``nearest_station_km`` means "how far is the evidence from here", which is
        a monitor distance for station data and a model-grid offset for a
        modelled source. A single 45 km grid cell means a point is a *model*
        estimate with no within-city variation, so the thresholds below would
        otherwise call a whole city "high confidence" just because the grid
        snapped nearby.
        """
        if self.is_estimated:
            return Confidence.LOW
        if self.source == "open_meteo_cams":
            # One grid cell per city: useful for timing, never street-level.
            return Confidence.LOW
        if self.nearest_station_km <= 2.0:
            return Confidence.HIGH
        if self.nearest_station_km <= 6.0:
            return Confidence.MEDIUM
        return Confidence.LOW


@runtime_checkable
class GridProvider(Protocol):
    """Source of pollutant concentrations for arbitrary points."""

    name: str

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]: ...

    async def aclose(self) -> None: ...
