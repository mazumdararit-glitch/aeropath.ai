"""National AQI from a time series of hourly concentrations.

``cpcb_aqi.compute`` takes one set of concentrations and turns it into an index.
What it cannot do is decide *which* concentrations — and CPCB's rules require
different averaging periods per pollutant:

* PM2.5, PM10, NO2, SO2, NH3 → **24-hour mean**
* CO, Ozone → **8-hour mean**

A single hourly sample is not a national AQI. Treating one as if it were would
report a rush-hour spike as the day's air quality, which is the kind of
confident-and-wrong answer this codebase is built to avoid. This module builds
the correct windows first, then delegates.

Incomplete windows are handled explicitly rather than by silently shortening
them: a caller decides whether a 12-of-24-hour mean is acceptable. The default
is to require a minimum fraction, because a mean over three hours is not a
24-hour mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError

# Averaging period per pollutant, from CPCB's published method.
AVERAGING_HOURS: dict[str, int] = {
    "PM2.5": 24,
    "PM10": 24,
    "NO2": 24,
    "SO2": 24,
    "NH3": 24,
    "CO": 8,
    "Ozone": 8,
}

# A window with fewer than this fraction of its hours is not that average.
DEFAULT_MIN_COVERAGE = 0.75


class IncompleteWindowError(ValueError):
    """A rolling window does not have enough hours to be that average."""


@dataclass(frozen=True, slots=True)
class WindowStats:
    """Coverage per pollutant, so a caller can see how complete the AQI is."""

    hours_available: dict[str, int]
    hours_required: dict[str, int]

    def coverage(self, pollutant: str) -> float:
        required = self.hours_required.get(pollutant, 0)
        if required <= 0:
            return 0.0
        return self.hours_available.get(pollutant, 0) / required


def _window_mean(
    series: dict[datetime, float],
    end: datetime,
    hours: int,
    *,
    min_coverage: float,
) -> tuple[float | None, int]:
    """Mean of the ``hours`` ending at ``end``, plus how many hours were present."""
    start = end - timedelta(hours=hours - 1)
    values = [value for hour, value in series.items() if start <= hour <= end]
    if not values:
        return None, 0
    if len(values) / hours < min_coverage:
        return None, len(values)
    return sum(values) / len(values), len(values)


def rolling_aqi(
    series: dict[str, dict[datetime, float]],
    *,
    at: datetime,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    require_minimum: bool = True,
) -> cpcb_aqi.AqiResult:
    """Build CPCB-mandated rolling averages ending at ``at``, then compute the AQI.

    ``series`` maps pollutant to ``{hour: concentration}``. Concentrations must
    already be in the units the CPCB anchors expect — see ``domain.units``.
    """
    concentrations: dict[str, float] = {}
    for pollutant, hourly in series.items():
        hours = AVERAGING_HOURS.get(pollutant)
        if hours is None:
            continue
        mean, _ = _window_mean(hourly, at, hours, min_coverage=min_coverage)
        if mean is not None:
            concentrations[pollutant] = mean

    if require_minimum and len(concentrations) < cpcb_aqi.MINIMUM_POLLUTANTS:
        raise InsufficientDataError(
            f"AQI needs {cpcb_aqi.MINIMUM_POLLUTANTS} pollutants with complete "
            f"averaging windows, got {len(concentrations)}: "
            f"{sorted(concentrations)}"
        )
    return cpcb_aqi.compute(concentrations, require_minimum=require_minimum)


def window_stats(
    series: dict[str, dict[datetime, float]], *, at: datetime
) -> WindowStats:
    """How complete each pollutant's averaging window is at ``at``."""
    available: dict[str, int] = {}
    required: dict[str, int] = {}
    for pollutant, hourly in series.items():
        hours = AVERAGING_HOURS.get(pollutant)
        if hours is None:
            continue
        required[pollutant] = hours
        start = at - timedelta(hours=hours - 1)
        available[pollutant] = sum(1 for hour in hourly if start <= hour <= at)
    return WindowStats(hours_available=available, hours_required=required)


def hourly_mean_series(
    readings: list[tuple[str, datetime, float]],
) -> dict[str, dict[datetime, float]]:
    """Pivot ``(pollutant, hour, value)`` rows into the mapping ``rolling_aqi`` wants.

    Multiple readings in the same hour are averaged, which is what a station
    reporting every 15 minutes needs before it can contribute to an hourly mean.
    """
    buckets: dict[str, dict[datetime, list[float]]] = {}
    for pollutant, hour, value in readings:
        buckets.setdefault(pollutant, {}).setdefault(hour, []).append(value)
    return {
        pollutant: {
            hour: sum(values) / len(values) for hour, values in hourly.items()
        }
        for pollutant, hourly in buckets.items()
    }
