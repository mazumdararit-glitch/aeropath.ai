"""Plan a walk or run: rank routes by exposure, then find the better hour.

Orchestration only. Every number that reaches a user is produced by
``services.scoring``; this module decides what to ask it and how to present the
answer. That separation is why the scoring engine stays pure and testable.

Two design commitments visible here:

* **``basis`` is always stated.** Whether an answer comes from measured data or
  from an hour-of-day climatology is not a detail; a user deciding whether to run
  now needs to know which one they are looking at.
* **Missing cells are excluded, not zeroed.** If a segment has no data, the
  engine drops it from the mean and reports ``unscored_fraction``. Scoring
  unknown air as clean would systematically favour routes through blind spots.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.domain.national_aqi import AVERAGING_HOURS, rolling_aqi
from aeropath.external.osrm import OsrmRouter, RoutingError
from aeropath.external.provider import GridProvider, PointReading
from aeropath.schemas.common import (
    AQCategory,
    Activity,
    Advisory,
    Confidence,
    Coordinate,
    Sensitivity,
)
from aeropath.services import routing
from aeropath.services.scoring import categories, engine, street_model

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    """One route, scored."""

    distance_m: float
    duration_s: float
    score: engine.RouteScore
    aqi_confidence: Confidence
    basis: str


@dataclass(frozen=True, slots=True)
class HourWindow:
    """A recommended hour, and what the recommendation is based on."""

    hour: int
    mean_aqi: float
    category: AQCategory
    dose: float
    samples: int
    basis: str


@dataclass(frozen=True, slots=True)
class Plan:
    routes: tuple[PlannedRoute, ...]
    best_windows: tuple[HourWindow, ...]
    generated_at: datetime
    data_source: str


class ClimatologyProfile:
    """Typical concentration by hour of day, for cities with no forecast.

    XKDR publishes measurements, not forecasts, so "when should I run today?"
    cannot be answered with a real forecast at no cost. What *can* be answered
    honestly is "when is it usually cleaner", from history. That is genuinely
    useful in Indian cities, where the diurnal cycle is strong and stable.

    The output is labelled ``climatology`` everywhere it surfaces, with its
    sample count, so it is never mistaken for a forecast.
    """

    def __init__(self, hourly_aqi: dict[int, list[float]]) -> None:
        self._hourly = {hour: values for hour, values in hourly_aqi.items() if values}

    @classmethod
    def from_series(
        cls,
        series: dict[str, dict[datetime, float]],
        *,
        min_coverage: float = 0.75,
    ) -> ClimatologyProfile:
        """Build the profile by computing an AQI at every hour that has data."""
        by_hour: dict[int, list[float]] = {}
        hours = sorted({hour for values in series.values() for hour in values})
        for hour in hours:
            try:
                result = rolling_aqi(series, at=hour, min_coverage=min_coverage)
            except InsufficientDataError:
                continue
            by_hour.setdefault(hour.hour, []).append(result.aqi)
        return cls(by_hour)

    @property
    def is_empty(self) -> bool:
        return not self._hourly

    def typical_aqi(self, hour: int) -> tuple[float, int] | None:
        """Median AQI at this hour, and how many observations it rests on."""
        values = self._hourly.get(hour % 24)
        if not values:
            return None
        return statistics.median(values), len(values)

    def cleanest_hours(self, *, limit: int = 3) -> list[tuple[int, float, int]]:
        """Hours ranked by typical AQI, cleanest first."""
        ranked: list[tuple[int, float, int]] = []
        for hour in range(24):
            typical = self.typical_aqi(hour)
            if typical is None:
                continue
            ranked.append((hour, typical[0], typical[1]))
        ranked.sort(key=lambda item: (item[1], item[0]))
        return ranked[:limit]


@dataclass(frozen=True, slots=True)
class PlanRequest:
    origin: Coordinate
    destination: Coordinate
    activity: Activity
    sensitivity: Sensitivity
    alternate_routes: int = 2


class PlanService:
    def __init__(self, provider: GridProvider, router: OsrmRouter) -> None:
        self._provider = provider
        self._router = router

    async def plan(
        self,
        request: PlanRequest,
        *,
        at: datetime | None = None,
        climatology: ClimatologyProfile | None = None,
    ) -> Plan:
        moment = at or datetime.now(UTC)

        geometries = await self._router.route(
            request.origin,
            request.destination,
            profile=routing.activity_profile(request.activity),
            alternatives=request.alternate_routes,
        )

        planned: list[PlannedRoute] = []
        for geometry in geometries:
            segments = routing.segments_from_geometry(geometry)
            readings = await self._provider.concentrations_at(
                [segment.coordinate for segment in segments], at=moment
            )
            engine_segments = self._to_engine_segments(segments, readings)

            try:
                score = engine.score_route(
                    engine_segments,
                    params=street_model.IDENTITY_MODEL,
                    hour_of_day=moment.hour,
                    activity=request.activity,
                    sensitivity=request.sensitivity,
                )
            except ValueError as exc:
                logger.warning("route could not be scored: %s", exc)
                continue

            planned.append(
                PlannedRoute(
                    distance_m=geometry.distance_m,
                    duration_s=geometry.duration_s,
                    score=score,
                    aqi_confidence=self._worst_confidence(readings),
                    basis="measured",
                )
            )

        if not planned:
            raise RoutingError(
                "no route could be scored: no grid data covered any segment"
            )

        ranked = engine.rank_routes([route.score for route in planned])
        # rank_routes returns the same RouteScore objects reordered, so matching on
        # identity keeps each score attached to its own distance/duration.
        ordered_by_id = {id(route.score): route for route in planned}
        ordered = tuple(ordered_by_id[id(score)] for score in ranked)

        windows = self._windows(
            ordered[0].score.mean_aqi, request.activity, climatology
        )

        return Plan(
            routes=ordered,
            best_windows=windows,
            generated_at=moment,
            data_source=self._provider.name,
        )

    def _to_engine_segments(
        self,
        segments: list[routing.RouteSegment],
        readings: list[PointReading],
    ) -> list[engine.SegmentInput]:
        """Join each segment to its reading and its derived AQI."""
        if len(segments) != len(readings):
            raise ValueError(
                f"segment/reading mismatch: {len(segments)} vs {len(readings)}"
            )

        out: list[engine.SegmentInput] = []
        for segment, reading in zip(segments, readings, strict=True):
            base_aqi: float | None = None
            if reading.concentrations:
                try:
                    base_aqi = cpcb_aqi.compute(reading.concentrations).aqi
                except InsufficientDataError:
                    # Too few pollutants for a national AQI. Treat as missing so
                    # the engine excludes the segment rather than guessing.
                    base_aqi = None
            out.append(
                engine.SegmentInput(
                    segment_id=segment.segment_id,
                    length_m=segment.length_m,
                    base_aqi=base_aqi,
                    road_class=routing.NEUTRAL_ROAD_CLASS,
                )
            )
        return out

    @staticmethod
    def _worst_confidence(readings: list[PointReading]) -> Confidence:
        """A route is only as trustworthy as its least-supported segment."""
        order = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
        if not readings:
            return Confidence.LOW
        return max((reading.confidence for reading in readings), key=order.__getitem__)

    def _windows(
        self,
        current_aqi: float,
        activity: Activity,
        climatology: ClimatologyProfile | None,
    ) -> tuple[HourWindow, ...]:
        """Recommended hours, from climatology when available."""
        if climatology is None or climatology.is_empty:
            return ()
        windows: list[HourWindow] = []
        for hour, typical, samples in climatology.cleanest_hours(limit=3):
            windows.append(
                HourWindow(
                    hour=hour,
                    mean_aqi=typical,
                    category=categories.classify(typical),
                    dose=engine.dose_for_duration(
                        typical,
                        activity=activity,
                        duration_min=30.0,
                        hour=None,  # type: ignore[arg-type]
                    ),
                    samples=samples,
                    basis="climatology",
                )
            )
        return tuple(windows)


def hours_for_parameter(pollutant: str) -> int | None:
    return AVERAGING_HOURS.get(pollutant)
