"""Route candidate generation, Pareto filtering, and 3-way labeling (Sprint 2 - R2)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from aeropath.schemas.common import Coordinate


@dataclass(frozen=True, slots=True)
class CandidateRoute:
    """A candidate route evaluated on duration, distance, and inhaled dose."""

    distance_m: float
    duration_s: float
    dose: float
    mean_aqi: float
    route_label: str = ""
    geometry: tuple[Coordinate, ...] = ()
    extra: Any = None


def generate_corridor_waypoints(
    origin: Coordinate,
    destination: Coordinate,
    offset_ratio: float = 0.15,
) -> list[Coordinate]:
    """Generate perpendicular offset waypoints to explore alternate low-exposure corridors."""
    d_lat = destination.lat - origin.lat
    d_lng = destination.lng - origin.lng

    mid_lat = (origin.lat + destination.lat) / 2.0
    mid_lng = (origin.lng + destination.lng) / 2.0

    # Perpendicular vector in (lat, lng)
    perp_lat = -d_lng * offset_ratio
    perp_lng = d_lat * offset_ratio

    left_wp = Coordinate(lat=mid_lat + perp_lat, lng=mid_lng + perp_lng)
    right_wp = Coordinate(lat=mid_lat - perp_lat, lng=mid_lng - perp_lng)
    return [left_wp, right_wp]


def filter_pareto_non_dominated(candidates: list[CandidateRoute]) -> list[CandidateRoute]:
    """Prune candidate routes that are strictly dominated in both time and dose.

    A route A dominates route B if:
    duration_s(A) <= duration_s(B) and dose(A) <= dose(B) with at least one strict inequality.
    """
    non_dominated: list[CandidateRoute] = []

    for c in candidates:
        is_dominated = False
        for other in candidates:
            if other is c:
                continue
            better_or_equal_time = other.duration_s <= c.duration_s
            better_or_equal_dose = other.dose <= c.dose
            strictly_better = (other.duration_s < c.duration_s) or (other.dose < c.dose)

            if better_or_equal_time and better_or_equal_dose and strictly_better:
                is_dominated = True
                break

        if not is_dominated:
            non_dominated.append(c)

    return non_dominated


def label_three_routes(
    candidates: list[CandidateRoute],
    *,
    max_detour_pct: float = 20.0,
    balanced_min_reduction_pct: float = 15.0,
    balanced_max_dist_pct: float = 10.0,
) -> list[CandidateRoute]:
    """Identify and label up to 3 Pareto candidates: Direct, Max Clean Air, Balanced."""
    if not candidates:
        return []

    # 1. Direct route: fastest travel time
    direct = min(candidates, key=lambda c: (c.duration_s, c.distance_m))
    labeled: dict[str, CandidateRoute] = {
        "Direct": replace(direct, route_label="Direct"),
    }

    base_dist = direct.distance_m
    base_dose = direct.dose
    max_dist_cap = base_dist * (1.0 + max_detour_pct / 100.0)

    # 2. Max Clean Air: lowest dose within detour cap
    eligible_clean = [c for c in candidates if c.distance_m <= max_dist_cap]
    if eligible_clean:
        cleanest = min(eligible_clean, key=lambda c: (c.dose, c.duration_s))
        if cleanest is not direct:
            labeled["Max Clean Air"] = replace(cleanest, route_label="Max Clean Air")

    # 3. Balanced: largest dose reduction per extra minute within modest distance increase
    balanced_max_dist = base_dist * (1.0 + balanced_max_dist_pct / 100.0)
    eligible_balanced: list[tuple[float, CandidateRoute]] = []

    for c in candidates:
        if c is direct or (eligible_clean and c is cleanest):
            continue
        if c.distance_m > balanced_max_dist:
            continue

        dose_reduction_pct = 100.0 * (base_dose - c.dose) / base_dose if base_dose > 0 else 0.0
        if dose_reduction_pct < balanced_min_reduction_pct:
            continue

        extra_minutes = (c.duration_s - direct.duration_s) / 60.0
        if extra_minutes <= 0.01:
            efficiency = dose_reduction_pct
        else:
            efficiency = dose_reduction_pct / extra_minutes

        eligible_balanced.append((efficiency, c))

    if eligible_balanced:
        eligible_balanced.sort(key=lambda item: item[0], reverse=True)
        best_balanced = eligible_balanced[0][1]
        labeled["Balanced"] = replace(best_balanced, route_label="Balanced")

    return list(labeled.values())
