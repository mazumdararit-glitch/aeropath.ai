"""Inverse-distance weighting from monitoring stations to arbitrary points.

This lives in ``domain`` rather than in the validation script because the
harness and the product must interpolate identically. If they diverged, Phase 0
would be reporting accuracy for a transformation the app does not perform — the
exact class of bug the harness exists to prevent.

Two properties the callers depend on:

* **Co-located stations do not divide by zero.** Delhi publishes Lodhi Road and
  Pusa twice, under two IDs at identical coordinates. A zero distance makes the
  ``1/d**2`` weight infinite. Such a station collapses to weight 1 and dominates,
  which is the correct behaviour: it *is* the measurement at that point.
* **Distance is reported, not just used.** The caller turns nearest-neighbour
  distance into a confidence level, so a point 40 km from any monitor cannot be
  presented with the same authority as one 500 m away.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from aeropath.schemas.common import Coordinate

EARTH_RADIUS_KM = 6371.0088

# Below this, two stations are the same physical measurement. 50 m is far under
# any real network's spacing and far over coordinate rounding noise.
COLOCATED_KM = 0.05

# Beyond this a neighbour says almost nothing about a point; including it anyway
# would attribute one city's air to another.
MAX_NEIGHBOUR_KM = 25.0


def haversine_km(a: Coordinate, b: Coordinate) -> float:
    """Great-circle distance in kilometres."""
    lat1, lon1, lat2, lon2 = map(radians, (a.lat, a.lng, b.lat, b.lng))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(h))


def inverse_distance_weights(
    target: Coordinate,
    others: dict[str, Coordinate],
    *,
    power: float = 2.0,
    max_distance_km: float = MAX_NEIGHBOUR_KM,
) -> dict[str, float]:
    """IDW weights for every station close enough to inform ``target``."""
    weights: dict[str, float] = {}
    for station_id, coordinate in others.items():
        distance = haversine_km(target, coordinate)
        if distance <= COLOCATED_KM:
            # A co-located station is the measurement at this point; it dominates,
            # and this also avoids a division by zero.
            return {station_id: 1.0}
        if distance > max_distance_km:
            continue
        weights[station_id] = 1.0 / distance**power
    return weights


def interpolate(values: dict[str, float], weights: dict[str, float]) -> float | None:
    """Weighted mean of the values that have a weight. ``None`` if nothing does."""
    usable = {sid: w for sid, w in weights.items() if sid in values}
    if not usable:
        return None
    total = sum(usable.values())
    if total <= 0:
        return None
    return sum(values[sid] * w for sid, w in usable.items()) / total


@dataclass(frozen=True, slots=True)
class InterpolatedPoint:
    """Concentrations at a point, with the evidence behind them."""

    concentrations: dict[str, float]
    nearest_station_km: float
    stations_used: int

    @property
    def usable(self) -> bool:
        return bool(self.concentrations)


def interpolate_point(
    target: Coordinate,
    stations: dict[str, Coordinate],
    series: dict[str, dict[str, float]],
    *,
    parameters: list[str],
    power: float = 2.0,
    max_distance_km: float = MAX_NEIGHBOUR_KM,
) -> InterpolatedPoint:
    """Interpolate a set of parameters at one point.

    ``series`` maps parameter name to ``{station_id: value}``. Parameters with no
    nearby measurement are omitted rather than invented, so the caller's
    ``cpcb_aqi.compute`` sees only what actually exists.
    """
    weights = inverse_distance_weights(
        target, stations, power=power, max_distance_km=max_distance_km
    )
    if not weights:
        return InterpolatedPoint(
            concentrations={}, nearest_station_km=float("inf"), stations_used=0
        )

    distances = [haversine_km(target, stations[sid]) for sid in weights if sid in stations]
    nearest = min(distances) if distances else float("inf")

    concentrations: dict[str, float] = {}
    for parameter in parameters:
        station_values = series.get(parameter, {})
        value = interpolate(station_values, weights)
        if value is not None:
            concentrations[parameter] = value

    return InterpolatedPoint(
        concentrations=concentrations,
        nearest_station_km=nearest,
        stations_used=len(weights),
    )
