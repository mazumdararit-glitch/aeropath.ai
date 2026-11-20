"""The exposure scoring engine.

This is the single most important module in the system: PRD 9.3, FR2, FR3 and
FR6 all resolve here, and the planner is only allowed to explain numbers this
module produced.

Design constraints:

* **Pure.** No database, no network, no clock. Phase 0 must be able to validate
  the model before any infrastructure exists, and the tests must be able to run
  the whole thing offline.
* **Two numbers, never one.** The PRD's formula divides by total length, which
  ranks routes by *mean* concentration and silently cancels ``activity_factor``.
  We publish ``mean_aqi`` (drives the colour) and ``dose`` (drives ranking)
  separately. See ``RouteScore``.
* **Deterministic.** Identical input must produce byte-identical output, so
  golden fixtures can be compared with a diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aeropath.schemas.common import (
    AQCategory,
    Activity,
    Advisory,
    RoadClass,
    Sensitivity,
)
from aeropath.services.scoring import categories
from aeropath.services.scoring.street_model import StreetModelParams, adjust

# Activity factors: air breathed per minute relative to walking. Running is
# roughly 3x the minute ventilation of an easy walk.
ACTIVITY_FACTORS: dict[Activity, float] = {
    Activity.WALK: 1.0,
    Activity.RUN: 3.0,
}

# Two routes within this relative dose are considered equivalent, and the
# shorter one wins. Without a tolerance the ranking is decided by float noise
# across routes that are, to a user, equally clean.
DOSE_EQUIVALENCE_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class SegmentInput:
    """One piece of a route, already joined to its grid cell and OSM attributes."""

    segment_id: int
    length_m: float
    base_aqi: float | None
    road_class: RoadClass
    green_distance_m: float | None = None
    water_distance_m: float | None = None

    def __post_init__(self) -> None:
        if self.length_m < 0:
            raise ValueError("length_m must not be negative")
        if self.base_aqi is not None and self.base_aqi < 0:
            raise ValueError("base_aqi must not be negative")


@dataclass(frozen=True, slots=True)
class SegmentScore:
    segment_id: int
    length_m: float
    base_aqi: float | None
    adjusted_aqi: float
    category: AQCategory
    factors: dict[str, float]


@dataclass(frozen=True, slots=True)
class RouteScore:
    """A scored route.

    ``mean_aqi`` answers "how polluted is this route on average" and drives the
    colour. ``dose`` answers "how much pollution does this route put into this
    person" and drives the ranking and the best-window search.

    They are not interchangeable. A long, gently polluted detour can have a
    lower mean than a short, filthy shortcut while delivering a higher total
    dose — and the dose is what a runner actually inhales. Ranking by mean
    while the forecast search ranks by dose would also let the app contradict
    itself between two screens.
    """

    segments: tuple[SegmentScore, ...]
    distance_m: float
    mean_aqi: float
    dose: float
    category: AQCategory
    activity: Activity
    advisory: Advisory
    worst_segment_id: int | None
    unscored_fraction: float

    @property
    def magnitude_km(self) -> float:
        return self.distance_m / 1000.0


def score_route(
    segments: list[SegmentInput] | tuple[SegmentInput, ...],
    *,
    params: StreetModelParams,
    hour_of_day: int,
    activity: Activity,
    sensitivity: Sensitivity,
    weather_factor: float = 1.0,
) -> RouteScore:
    """Score a route by air pollution exposure.

    ``base_aqi`` of ``None`` means the grid had no value for that cell. Those
    segments are excluded from the mean rather than treated as zero, because
    scoring missing data as clean air would systematically favour routes whose
    cells happen to be missing.
    """
    if not segments:
        raise ValueError("cannot score an empty route")

    activity_factor = ACTIVITY_FACTORS[activity]

    scored: list[SegmentScore] = []
    total_length = 0.0
    scored_length = 0.0
    weighted_sum = 0.0

    for segment in segments:
        total_length += segment.length_m
        if segment.base_aqi is None:
            continue

        adjusted_aqi, factors = adjust(
            segment.base_aqi,
            params,
            road_class=segment.road_class,
            hour_of_day=hour_of_day,
            green_distance_m=segment.green_distance_m,
            water_distance_m=segment.water_distance_m,
            weather_factor=weather_factor,
        )
        scored.append(
            SegmentScore(
                segment_id=segment.segment_id,
                length_m=segment.length_m,
                base_aqi=segment.base_aqi,
                adjusted_aqi=adjusted_aqi,
                category=categories.classify(adjusted_aqi),
                factors=factors,
            )
        )
        scored_length += segment.length_m
        weighted_sum += adjusted_aqi * segment.length_m

    if scored_length == 0.0 or total_length == 0.0:
        raise ValueError("route has no scoreable length")

    mean_aqi = weighted_sum / scored_length
    dose = weighted_sum * activity_factor
    category = categories.classify(mean_aqi)

    worst = max(scored, key=lambda s: s.adjusted_aqi, default=None)

    return RouteScore(
        segments=tuple(scored),
        distance_m=total_length,
        mean_aqi=mean_aqi,
        dose=dose,
        category=category,
        activity=activity,
        advisory=categories.advisory(category, sensitivity),
        worst_segment_id=None if worst is None else worst.segment_id,
        unscored_fraction=1.0 - (scored_length / total_length),
    )


def rank_routes(scores: list[RouteScore] | tuple[RouteScore, ...]) -> list[RouteScore]:
    """Order routes by exposure, cleanest first.

    Sorted by ``dose``, with a deterministic tiebreak: when two routes are
    within ``DOSE_EQUIVALENCE_TOLERANCE`` of each other, the shorter one wins.
    Without that, a 12 km route and a 5 km route that differ by float noise
    would be ordered arbitrarily, and the user would be sent on the long one.
    """
    if not scores:
        return []

    ordered = sorted(scores, key=lambda s: (s.dose, s.distance_m, s.segments[0].segment_id))
    result = list(ordered)

    # Stable pass: prefer the shorter route among dose-equivalent neighbours.
    i = 0
    while i < len(result) - 1:
        j = i + 1
        while (
            j < len(result)
            and _relative_gap(result[i].dose, result[j].dose) <= DOSE_EQUIVALENCE_TOLERANCE
        ):
            j += 1
        if j - i > 1:
            result[i:j] = sorted(result[i:j], key=lambda s: s.distance_m)
        i = j

    return result


def _relative_gap(a: float, b: float) -> float:
    if a == 0.0 and b == 0.0:
        return 0.0
    denominator = max(a, b)
    return abs(a - b) / denominator if denominator else 0.0


def dose_for_duration(
    mean_aqi: float,
    *,
    activity: Activity,
    duration_min: float,
    hour: datetime,
) -> float:
    """Dose for a stay in one place, used by the best-window search.

    Same definition as ``RouteScore.dose``: concentration times time, scaled by
    how hard the person is breathing. Expressed in AQI-minutes so a route and a
    time window are directly comparable.
    """
    if duration_min <= 0:
        raise ValueError("duration_min must be positive")
    del hour  # present for signature symmetry; dose is time-invariant
    return mean_aqi * duration_min * ACTIVITY_FACTORS[activity]
