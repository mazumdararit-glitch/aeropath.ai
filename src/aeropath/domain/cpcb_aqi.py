"""CPCB National AQI computation from pollutant concentrations.

The monitoring network publishes **concentrations**, not a ready-made index, so
comparing our estimates against station data requires recomputing the index the
same way CPCB does. Getting this wrong would invalidate every number in the
Phase 0 gate, so the breakpoints are stored as explicit anchor points and the
sub-index interpolates between adjacent anchors. That form makes each published
band edge exact by construction — PM2.5 of 30 µg/m³ lands on AQI 50, not 51.

Rules that matter (CPCB, "About National AQI"):

* Each pollutant gets a **sub-index** by linear interpolation inside its
  breakpoint band.
* The overall AQI is the **maximum** sub-index, not a mean.
* An AQI is only reportable with **at least three pollutants**, one of which
  must be PM2.5 or PM10.
* Averaging periods differ: PM, NO2 and SO2 use 24-hour means; CO and O3 use
  8-hour means. A single hourly sample is **not** a national AQI — this module
  computes from whatever averages the caller supplies and never pretends an
  hourly sample is a national AQI.
"""

from __future__ import annotations

from dataclasses import dataclass

from aeropath.schemas.common import AQCategory

# Anchor points per pollutant: (concentration, AQI) at each band boundary.
# First anchor is always (0, 0); the last is where the scale reaches 500.
_ANCHORS: dict[str, tuple[tuple[float, float], ...]] = {
    # 24-hour mean, µg/m³. 0-30, 31-60, 61-90, 91-120, 121-250, 251+.
    "PM2.5": (
        (0.0, 0.0),
        (30.0, 50.0),
        (60.0, 100.0),
        (90.0, 200.0),
        (120.0, 300.0),
        (250.0, 400.0),
        (500.0, 500.0),
    ),
    # 24-hour mean, µg/m³. 0-50, 51-100, 101-250, 251-350, 351-430, 431+.
    "PM10": (
        (0.0, 0.0),
        (50.0, 50.0),
        (100.0, 100.0),
        (250.0, 200.0),
        (350.0, 300.0),
        (430.0, 400.0),
        (600.0, 500.0),
    ),
    # 24-hour mean, µg/m³. 0-40, 41-80, 81-180, 181-280, 281-400, 401+.
    "NO2": (
        (0.0, 0.0),
        (40.0, 50.0),
        (80.0, 100.0),
        (180.0, 200.0),
        (280.0, 300.0),
        (400.0, 400.0),
        (600.0, 500.0),
    ),
    # 8-hour mean, mg/m³. 0-1.0, 1.1-2.0, 2.1-10, 10.1-17, 17.1-34, 34+.
    "CO": (
        (0.0, 0.0),
        (1.0, 50.0),
        (2.0, 100.0),
        (10.0, 200.0),
        (17.0, 300.0),
        (34.0, 400.0),
        (50.0, 500.0),
    ),
    # 8-hour mean, µg/m³. 0-50, 51-100, 101-168, 169-208, 209-748, 749+.
    "Ozone": (
        (0.0, 0.0),
        (50.0, 50.0),
        (100.0, 100.0),
        (168.0, 200.0),
        (208.0, 300.0),
        (748.0, 400.0),
        (1000.0, 500.0),
    ),
    # 24-hour mean, µg/m³. 0-40, 41-80, 81-380, 381-800, 801-1600, 1601+.
    "SO2": (
        (0.0, 0.0),
        (40.0, 50.0),
        (80.0, 100.0),
        (380.0, 200.0),
        (800.0, 300.0),
        (1600.0, 400.0),
        (2000.0, 500.0),
    ),
    # 24-hour mean, µg/m³. 0-200, 201-400, 401-800, 801-1200, 1201-1800, 1801+.
    "NH3": (
        (0.0, 0.0),
        (200.0, 50.0),
        (400.0, 100.0),
        (800.0, 200.0),
        (1200.0, 300.0),
        (1800.0, 400.0),
        (2400.0, 500.0),
    ),
}

# Values at or below the detection floor are indistinguishable from zero. They
# anchor to the bottom of the scale rather than producing a sub-index that looks
# like a measurement but is noise.
_DETECTION_FLOORS: dict[str, float] = {
    "PM2.5": 0.05,
    "PM10": 0.05,
    "NO2": 0.05,
    "CO": 0.005,
    "Ozone": 0.05,
    "SO2": 0.05,
    "NH3": 0.05,
}

# CPCB requires one of these for an AQI to be reportable.
_PARTICULATE = frozenset({"PM2.5", "PM10"})

# Pollutant labels vary across sources (CPCB portal, XKDR, Open-Meteo).
_ALIASES: dict[str, str] = {
    "pm2.5": "PM2.5",
    "pm25": "PM2.5",
    "pm2": "PM2.5",
    "pm10": "PM10",
    "no2": "NO2",
    "co": "CO",
    "ozone": "Ozone",
    "o3": "Ozone",
    "so2": "SO2",
    "nh3": "NH3",
}

SUPPORTED = frozenset(_ANCHORS)
MINIMUM_POLLUTANTS = 3
AQI_CEILING = 500.0


class InsufficientDataError(ValueError):
    """Not enough pollutants to report a national AQI.

    Raised rather than returning a value from one or two pollutants, which is
    how a single PM2.5 reading becomes a confident, wrong colour.
    """


@dataclass(frozen=True, slots=True)
class SubIndex:
    pollutant: str
    concentration: float
    sub_index: float
    category: AQCategory


@dataclass(frozen=True, slots=True)
class AqiResult:
    aqi: float
    category: AQCategory
    dominant_pollutant: str
    sub_indices: tuple[SubIndex, ...]

    @property
    def pollutant_count(self) -> int:
        return len(self.sub_indices)


def canonical_pollutant(name: str) -> str | None:
    """Normalise a pollutant label, or ``None`` if unsupported."""
    if name in _ANCHORS:
        return name
    return _ALIASES.get(name.strip().lower())


def sub_index(pollutant: str, concentration: float) -> float:
    """CPCB sub-index for one pollutant, linearly interpolated between anchors.

    Every published band edge is exact: PM2.5 of 30 gives 50, 60 gives 100,
    90 gives 200. Above the final anchor the scale saturates at 500 rather than
    extrapolating without bound.
    """
    canonical = canonical_pollutant(pollutant)
    if canonical is None:
        raise ValueError(f"unsupported pollutant: {pollutant}")
    if concentration < 0:
        raise ValueError("concentration must not be negative")

    if concentration <= _DETECTION_FLOORS[canonical]:
        return 0.0

    anchors = _ANCHORS[canonical]
    if concentration >= anchors[-1][0]:
        return AQI_CEILING

    for (low_c, low_aqi), (high_c, high_aqi) in zip(anchors, anchors[1:], strict=True):
        if concentration <= high_c:
            span = high_c - low_c
            if span <= 0:
                return float(high_aqi)
            interpolated = low_aqi + (concentration - low_c) / span * (high_aqi - low_aqi)
            return max(0.0, min(AQI_CEILING, interpolated))

    return AQI_CEILING


def compute(
    concentrations: dict[str, float],
    *,
    require_minimum: bool = True,
) -> AqiResult:
    """Compute the national AQI from a set of pollutant concentrations.

    ``require_minimum=False`` is for single-pollutant diagnostics only — never
    for anything a user sees.
    """
    from aeropath.services.scoring import categories as category_bands

    subs: list[SubIndex] = []
    for raw_name, value in concentrations.items():
        canonical = canonical_pollutant(raw_name)
        if canonical is None or value is None:
            continue
        value = float(value)
        if value < 0:
            continue
        computed = sub_index(canonical, value)
        subs.append(
            SubIndex(
                pollutant=canonical,
                concentration=value,
                sub_index=computed,
                category=category_bands.classify(computed),
            )
        )

    if require_minimum:
        if len(subs) < MINIMUM_POLLUTANTS:
            raise InsufficientDataError(
                f"AQI needs {MINIMUM_POLLUTANTS} pollutants, got {len(subs)}"
            )
        if not any(s.pollutant in _PARTICULATE for s in subs):
            raise InsufficientDataError("AQI needs PM2.5 or PM10")

    if not subs:
        raise InsufficientDataError("no usable pollutant concentrations")

    worst = max(subs, key=lambda s: s.sub_index)
    aqi = min(AQI_CEILING, max(0.0, worst.sub_index))

    return AqiResult(
        aqi=aqi,
        category=category_bands.classify(aqi),
        dominant_pollutant=worst.pollutant,
        sub_indices=tuple(sorted(subs, key=lambda s: s.pollutant)),
    )


def try_compute(concentrations: dict[str, float]) -> AqiResult | None:
    """``compute`` when the data allows it, ``None`` when it does not."""
    try:
        return compute(concentrations)
    except InsufficientDataError:
        return None
