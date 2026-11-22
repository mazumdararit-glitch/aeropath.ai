"""The street-level adjustment model.

This is the product's stated main differentiator *and* its stated main accuracy
risk (PRD 9.2). It lives in ``services/`` rather than ``intelligence/`` because
it is deterministic rules, not AI — folding it in with the LLM layer invites
exactly the confusion the PRD forbids.

**The model adjusts pollutant concentrations, not the AQI.**

PRD 9.3 specifies ``segment_aqi = base_aqi x road_factor x time_factor``, which
multiplies the finished index. That operation is wrong, and the error is large
rather than cosmetic: the CPCB sub-index is a piecewise-linear map whose bands
are *not* evenly spaced (PM2.5 bands are 30/30/30/30/130 µg/m³ wide), so
``aqi x 1.35`` and ``aqi(concentration x 1.35)`` disagree by up to 80 AQI
points, and in opposite directions in different bands. Scaling the index
therefore does not scale the air; it distorts it.

So every factor multiplies the **concentrations** and the index is recomputed
afterwards. That keeps the adjustment physically meaningful — a busy road really
does have more µg/m³ of PM2.5 — and keeps the result inside the published scale
by construction instead of by clamping.

Every constant is data, not code: production loads the active row from
``model_versions`` so a refit is a database change, not a deploy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from aeropath.domain import cpcb_aqi
from aeropath.schemas.common import RoadClass

# A 500 m provider cell cannot honestly support more than this. Without a cap,
# stacking factors can claim a large local adjustment that the underlying data
# has no resolution to justify — and that lands users in the wrong colour.
MAX_RELATIVE_ADJUSTMENT = 0.40


@dataclass(frozen=True, slots=True)
class StreetModelParams:
    """Tunable constants. Fitted in Phase 0, loaded from the DB afterwards."""

    version: str
    road_factors: dict[RoadClass, float] = field(default_factory=dict)
    green_attenuation: float = 0.15
    green_scale_m: float = 150.0
    water_attenuation: float = 0.10
    water_scale_m: float = 300.0
    rush_factor: float = 1.15
    night_factor: float = 0.85
    rush_hours: frozenset[int] = frozenset({8, 9, 10, 17, 18, 19, 20})
    night_hours: frozenset[int] = frozenset({23, 0, 1, 2, 3, 4, 5})
    # Sprint 3 (R3) - Physical dispersion parameters
    canyon_leeward_factor: float = 1.0
    wind_dispersion_exponent: float = 0.5
    vegetation_reduction_factor: float = 0.18
    vegetation_decay_length_m: float = 50.0
    water_reduction_factor: float = 0.10
    water_decay_length_m: float = 100.0


# Initial rules-of-thumb table. Phase 0 fits these against station readings;
# the values here are only the starting point for that fit.
DEFAULT_ROAD_FACTORS: dict[RoadClass, float] = {
    RoadClass.MOTORWAY: 1.35,
    RoadClass.TRUNK: 1.35,
    RoadClass.PRIMARY: 1.25,
    RoadClass.SECONDARY: 1.15,
    RoadClass.TERTIARY: 1.00,
    RoadClass.RESIDENTIAL: 1.00,
    RoadClass.LIVING_STREET: 0.90,
    RoadClass.PEDESTRIAN: 0.90,
    RoadClass.SERVICE: 0.90,
    RoadClass.FOOTWAY: 0.80,
    RoadClass.PATH: 0.80,
    RoadClass.CYCLEWAY: 0.80,
    RoadClass.UNKNOWN: 1.00,
}

DEFAULT_MODEL = StreetModelParams(
    version="rules_v1",
    road_factors=dict(DEFAULT_ROAD_FACTORS),
)

# What production uses.
#
# ``rules_v1`` above is kept for the Phase 0 harness and its tests, but it must
# not score a user-facing route: ``road_factor`` has the wrong sign and road
# class explains 12.8% of residual variance at p=0.429 (docs/phase0-results.md,
# finding 4). Every one of the six cities with enough stations to test it failed
# the gate with this model applied.
#
# This identity model is not "untuned" — it is the correct answer until a proxy
# that actually locates local variation exists. It scales every factor to 1.0,
# so the engine returns the interpolated value unchanged.
IDENTITY_MODEL = StreetModelParams(
    version="identity_v1",
    road_factors={road_class: 1.0 for road_class in RoadClass},
    green_attenuation=0.0,
    water_attenuation=0.0,
    rush_factor=1.0,
    night_factor=1.0,
)

# Street Model v2 (Sprint 3 - R3): off by default, incorporates microclimate
# wind direction/speed, street canyon vortex, and exponential vegetation/water decay.
RULES_V2_MODEL = StreetModelParams(
    version="rules_v2",
    road_factors=dict(DEFAULT_ROAD_FACTORS),
    green_attenuation=0.18,
    green_scale_m=50.0,
    water_attenuation=0.10,
    water_scale_m=100.0,
    rush_factor=1.15,
    night_factor=0.85,
    canyon_leeward_factor=1.4,
    wind_dispersion_exponent=0.5,
    vegetation_reduction_factor=0.18,
    vegetation_decay_length_m=50.0,
    water_reduction_factor=0.10,
    water_decay_length_m=100.0,
)


def proximity_v2_factor(distance_m: float, reduction: float, decay_m: float) -> float:
    """Decay toward 1.0 as distance increases according to exponential length scale."""
    if distance_m < 0:
        raise ValueError("distance_m must not be negative")
    return 1.0 - reduction * math.exp(-distance_m / decay_m)


def canyon_wind_factor(
    wind_speed_mps: float,
    wind_dir_deg: float,
    street_bearing_deg: float,
    params: StreetModelParams,
) -> float:
    """Calculate street canyon vortex and wind ventilation dispersion factor."""
    if wind_speed_mps < 0:
        raise ValueError("wind_speed_mps must not be negative")

    # Relative angle between wind vector and street orientation
    rel_angle = abs((wind_dir_deg - street_bearing_deg + 180) % 360 - 180)
    crosswind_fraction = math.sin(math.radians(rel_angle))

    # Crosswinds generate leeward recirculating vortex trapping pollutants
    vortex_enhancement = 1.0 + (params.canyon_leeward_factor - 1.0) * crosswind_fraction

    # Ventilation dilution from wind speed: higher wind speed flushes street canyon
    ventilation_dilution = 1.0 / (1.0 + (wind_speed_mps**params.wind_dispersion_exponent) * 0.25)

    factor = vortex_enhancement * ventilation_dilution
    return float(min(max(factor, 0.4), 2.0))


def road_factor(params: StreetModelParams, road_class: RoadClass) -> float:
    return params.road_factors.get(road_class, 1.00)


def proximity_factor(distance_m: float, attenuation: float, scale_m: float) -> float:
    """Decay toward 1.0 as distance grows.

    ``1 - attenuation`` at zero distance, approaching 1.0 asymptotically. A
    green area lowers the local concentration, so the factor is below 1.
    """
    if distance_m < 0:
        raise ValueError("distance_m must not be negative")
    return 1.0 - attenuation * math.exp(-distance_m / scale_m)


def green_factor(params: StreetModelParams, green_distance_m: float) -> float:
    return proximity_factor(green_distance_m, params.green_attenuation, params.green_scale_m)


def water_factor(params: StreetModelParams, water_distance_m: float) -> float:
    return proximity_factor(water_distance_m, params.water_attenuation, params.water_scale_m)


def time_factor(params: StreetModelParams, hour_of_day: int) -> float:
    """Rush-hour and overnight adjustment, in the activity's local time."""
    if not 0 <= hour_of_day <= 23:
        raise ValueError("hour_of_day must be 0-23")
    if hour_of_day in params.rush_hours:
        return params.rush_factor
    if hour_of_day in params.night_hours:
        return params.night_factor
    return 1.0


def combined_multiplier(
    params: StreetModelParams,
    *,
    road_class: RoadClass,
    hour_of_day: int,
    green_distance_m: float | None = None,
    water_distance_m: float | None = None,
    weather_factor: float = 1.0,
    max_relative_adjustment: float = MAX_RELATIVE_ADJUSTMENT,
) -> tuple[float, dict[str, float]]:
    """The total concentration multiplier, and the factors that built it."""
    if weather_factor <= 0:
        raise ValueError("weather_factor must be positive")

    factors = {
        "road": road_factor(params, road_class),
        "time": time_factor(params, hour_of_day),
    }
    if green_distance_m is not None:
        factors["green"] = green_factor(params, green_distance_m)
    if water_distance_m is not None:
        factors["water"] = water_factor(params, water_distance_m)
    factors["weather"] = weather_factor

    multiplier = math.prod(factors.values())
    capped = min(
        max(multiplier, 1.0 - max_relative_adjustment),
        1.0 + max_relative_adjustment,
    )
    return capped, factors


def adjust_concentrations(
    concentrations: dict[str, float],
    params: StreetModelParams,
    *,
    road_class: RoadClass,
    hour_of_day: int,
    green_distance_m: float | None = None,
    water_distance_m: float | None = None,
    weather_factor: float = 1.0,
    max_relative_adjustment: float = MAX_RELATIVE_ADJUSTMENT,
) -> tuple[dict[str, float], dict[str, float]]:
    """Scale pollutant concentrations, then let the caller recompute the index.

    Returns the scaled concentrations and the factors, because the planner is
    only allowed to explain numbers the engine actually produced — so the
    factors must be carried out of here, not recomputed later.
    """
    multiplier, factors = combined_multiplier(
        params,
        road_class=road_class,
        hour_of_day=hour_of_day,
        green_distance_m=green_distance_m,
        water_distance_m=water_distance_m,
        weather_factor=weather_factor,
        max_relative_adjustment=max_relative_adjustment,
    )
    scaled = {name: max(0.0, value * multiplier) for name, value in concentrations.items()}
    return scaled, factors


def adjust_aqi(
    aqi: float,
    params: StreetModelParams,
    *,
    road_class: RoadClass,
    hour_of_day: int,
    green_distance_m: float | None = None,
    water_distance_m: float | None = None,
    weather_factor: float = 1.0,
) -> tuple[float, dict[str, float]]:
    """Apply the model to a finished AQI when concentrations are unavailable.

    This is the *approximation*, and it exists only for the case where an
    upstream value is already an index with no pollutant breakdown (a grid cell
    from a provider that reports only AQI). It is not equivalent to adjusting
    concentrations — the CPCB scale is piecewise, so the two disagree, most
    sharply between 100 and 300 AQI. Prefer ``adjust_concentrations``.
    """
    if aqi < 0:
        raise ValueError("aqi must not be negative")

    multiplier, factors = combined_multiplier(
        params,
        road_class=road_class,
        hour_of_day=hour_of_day,
        green_distance_m=green_distance_m,
        water_distance_m=water_distance_m,
        weather_factor=weather_factor,
    )
    return max(0.0, min(cpcb_aqi.AQI_CEILING, aqi * multiplier)), factors


def adjust(
    base_aqi: float,
    params: StreetModelParams,
    *,
    road_class: RoadClass,
    hour_of_day: int,
    green_distance_m: float | None = None,
    water_distance_m: float | None = None,
    weather_factor: float = 1.0,
) -> tuple[float, dict[str, float]]:
    """Deprecated alias for :func:`adjust_aqi`. See its docstring for the caveat."""
    return adjust_aqi(
        base_aqi,
        params,
        road_class=road_class,
        hour_of_day=hour_of_day,
        green_distance_m=green_distance_m,
        water_distance_m=water_distance_m,
        weather_factor=weather_factor,
    )


def local_hour(moment: datetime) -> int:
    """Hour of day in the moment's own timezone.

    Naive datetimes are rejected rather than assumed to be UTC — a 5:30 offset
    would move the rush-hour window by more than five hours.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    return moment.hour
