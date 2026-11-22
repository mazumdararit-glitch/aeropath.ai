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
from datetime import UTC, datetime

from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.domain.national_aqi import AVERAGING_HOURS, rolling_aqi
from aeropath.external.osrm import OsrmRouter
from aeropath.external.provider import GridProvider, PointReading
from aeropath.schemas.common import (
    Activity,
    AQCategory,
    Confidence,
    Coordinate,
    Sensitivity,
)
from aeropath.services import routing
from aeropath.services.candidate_router import (
    CandidateRoute,
    generate_corridor_waypoints,
    label_three_routes,
)
from aeropath.services.scoring import categories, engine, street_model

logger = logging.getLogger(__name__)


class ForecastProfile:
    """Hourly AQI from a real forecast, for the best-window search.

    Unlike ``ClimatologyProfile`` (which answers "when is it usually cleaner"),
    this carries an actual forecast and is labelled ``forecast`` in every
    response so a user knows the difference.

    ``hourly_aqi`` maps hour-of-day (0-23) to the forecast AQI for that
    specific upcoming hour, not a historical distribution.
    """

    def __init__(self, hourly_aqi: dict[int, float]) -> None:
        self._hourly = {h: v for h, v in hourly_aqi.items() if v > 0}

    @property
    def is_empty(self) -> bool:
        return not self._hourly

    def cleanest_hours(self, *, limit: int = 3) -> list[tuple[int, float]]:
        """Hours ranked by forecast AQI, cleanest first."""
        ranked = sorted(self._hourly.items(), key=lambda item: (item[1], item[0]))
        return ranked[:limit]


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    """One route, scored."""

    distance_m: float
    duration_s: float
    score: engine.RouteScore
    aqi_confidence: Confidence
    basis: str
    geometry: tuple[Coordinate, ...] = ()
    # False when the index rests on fewer pollutants than CPCB requires, which is
    # the normal case for data after 2024-12. The number is then a particulate
    # sub-index, not a national AQI, and clients must say so.
    is_national_aqi: bool = True
    pollutants_used: int = 0
    is_estimated: bool = False
    relative_dose_pct: float = 0.0
    route_label: str = ""


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
    # The day the measurements describe. CPCB ingestion lags by months, so this
    # is routinely much older than generated_at and must never be assumed equal.
    data_date: str | None = None


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


class DataUnavailableError(RuntimeError):
    """A route was found, but no air-quality data covered it.

    Distinct from ``RoutingError`` on purpose: the router succeeded, so this is a
    data problem, not an upstream routing failure. Reporting it as 502 would send
    an operator to look at the wrong service.
    """


@dataclass(frozen=True, slots=True)
class PlanRequest:
    origin: Coordinate
    destination: Coordinate
    activity: Activity
    sensitivity: Sensitivity
    alternate_routes: int = 2


class PlanService:
    def __init__(
        self,
        provider: GridProvider,
        router: OsrmRouter,
        street_params: street_model.StreetModelParams = street_model.DEFAULT_MODEL,
    ) -> None:
        self._provider = provider
        self._router = router
        self._street_params = street_params
        # Set from the first reading that carries a timestamp, so a live provider
        # still reports which hour it modelled.
        self._last_observed_at: datetime | None = None

    async def plan(
        self,
        request: PlanRequest,
        *,
        at: datetime | None = None,
        climatology: ClimatologyProfile | None = None,
        forecast: ForecastProfile | None = None,
    ) -> Plan:
        moment = at or datetime.now(UTC)

        geometries = await self._router.route(
            request.origin,
            request.destination,
            profile=routing.activity_profile(request.activity),
            alternatives=request.alternate_routes,
        )

        if hasattr(self._router, "route_waypoints"):
            try:
                for wp in generate_corridor_waypoints(request.origin, request.destination):
                    detours = await self._router.route_waypoints(
                        [request.origin, wp, request.destination],
                        profile=routing.activity_profile(request.activity),
                    )
                    geometries.extend(detours)
            except Exception as exc:
                logger.debug("could not fetch corridor detour candidates: %s", exc)

        planned: list[PlannedRoute] = []
        sampled = 0
        with_concentrations = 0
        for geometry in geometries:
            segments = routing.segments_from_geometry(geometry)
            readings = await self._provider.concentrations_at(
                [segment.coordinate for segment in segments], at=moment
            )
            sampled += len(readings)
            with_concentrations += sum(1 for reading in readings if reading.concentrations)
            # Remember when the data describes, for the response's data_date.
            for reading in readings:
                if reading.observed_at is not None:
                    self._last_observed_at = reading.observed_at
                    break
            engine_segments = self._to_engine_segments(segments, readings)

            try:
                score = engine.score_route(
                    engine_segments,
                    params=self._street_params,
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
                    geometry=geometry.coordinates,
                    is_national_aqi=self._is_national(readings),
                    pollutants_used=max(
                        (len(reading.concentrations) for reading in readings),
                        default=0,
                    ),
                    is_estimated=any(reading.is_estimated for reading in readings),
                )
            )

        if not planned:
            # Distinguish "no data" from "no route". The router worked; the grid
            # simply does not cover this area, which is actionable information.
            raise DataUnavailableError(
                "no air-quality data covered this route. "
                f"{with_concentrations} of {sampled} sampled points had "
                "concentrations. CPCB coverage is sparse outside major cities, and "
                "the dataset lags by months - check /v1/coverage for the available "
                "window."
            )

        # Identify 3 labeled routes (Direct, Max Clean Air, Balanced)
        candidate_items = [
            CandidateRoute(
                distance_m=r.distance_m,
                duration_s=r.duration_s,
                dose=r.score.dose,
                mean_aqi=r.score.mean_aqi,
                extra=id(r),
            )
            for r in planned
        ]
        labeled_routes = label_three_routes(
            candidate_items,
            max_detour_pct=20.0,
            balanced_min_reduction_pct=15.0,
            balanced_max_dist_pct=10.0,
        )
        label_by_id = {c.extra: c.route_label for c in labeled_routes if c.extra is not None}

        fastest = min(planned, key=lambda r: r.duration_s)
        fastest_dose = fastest.score.dose
        planned_with_rel: list[PlannedRoute] = []
        for r in planned:
            rel = (
                round(100.0 * (r.score.dose - fastest_dose) / fastest_dose, 1)
                if fastest_dose > 0
                else 0.0
            )
            planned_with_rel.append(
                PlannedRoute(
                    distance_m=r.distance_m,
                    duration_s=r.duration_s,
                    score=r.score,
                    aqi_confidence=r.aqi_confidence,
                    basis=r.basis,
                    geometry=r.geometry,
                    is_national_aqi=r.is_national_aqi,
                    pollutants_used=r.pollutants_used,
                    is_estimated=r.is_estimated,
                    relative_dose_pct=rel,
                    route_label=label_by_id.get(id(r), ""),
                )
            )

        ranked = engine.rank_routes([route.score for route in planned_with_rel])
        # rank_routes returns the same RouteScore objects reordered, so matching on
        # identity keeps each score attached to its own distance/duration.
        ordered_by_id = {id(route.score): route for route in planned_with_rel}
        ordered = tuple(ordered_by_id[id(score)] for score in ranked)

        if forecast is None and climatology is None and hasattr(self._provider, "hourly_forecast"):
            try:
                hourly = await self._provider.hourly_forecast(request.origin, hours=96)
                if hourly:
                    hourly_aqi: dict[int, float] = {}
                    for offset, concentrations in hourly.items():
                        hour_of_day = (moment.hour + offset) % 24
                        try:
                            res = cpcb_aqi.compute(concentrations, require_minimum=False)
                            if hour_of_day not in hourly_aqi:
                                hourly_aqi[hour_of_day] = res.aqi
                        except InsufficientDataError:
                            continue
                    if hourly_aqi:
                        forecast = ForecastProfile(hourly_aqi)
            except Exception as exc:
                logger.debug("could not fetch forecast for best_windows: %s", exc)

        windows = self._windows(ordered[0].score.mean_aqi, request.activity, climatology, forecast)

        return Plan(
            routes=ordered,
            best_windows=windows,
            generated_at=moment,
            data_source=self._provider.name,
            data_date=self._data_date(),
        )

    def _data_date(self) -> str | None:
        """Which day/time the numbers describe, from whichever provider served.

        A lagging provider knows its archive day; a live one stamps the hour its
        model field describes. Either way the response carries a date rather than
        leaving "now" to be assumed.
        """
        resolved = getattr(self._provider, "resolved_day", None)
        if resolved is not None:
            return str(resolved.isoformat())
        observed = getattr(self, "_last_observed_at", None)
        return str(observed.isoformat()) if observed else None

    def _to_engine_segments(
        self,
        segments: list[routing.RouteSegment],
        readings: list[PointReading],
    ) -> list[engine.SegmentInput]:
        """Join each segment to its reading and its derived AQI.

        CPCB requires three pollutants for a national AQI, but the published
        dataset stops reporting gases after 2024-12, leaving PM2.5 and PM10 only.
        Rather than refuse to score — which would make the product unusable on all
        recent data — the particulate sub-index is used and the response is marked
        ``is_national_aqi=False``. Weaker and labelled beats absent.
        """
        if len(segments) != len(readings):
            raise ValueError(f"segment/reading mismatch: {len(segments)} vs {len(readings)}")

        out: list[engine.SegmentInput] = []
        for segment, reading in zip(segments, readings, strict=True):
            out.append(
                engine.SegmentInput(
                    segment_id=segment.segment_id,
                    length_m=segment.length_m,
                    base_aqi=self._index_for(reading),
                    road_class=segment.road_class,
                    pm25_concentration=reading.concentrations.get("PM2.5"),
                )
            )
        return out

    @staticmethod
    def _index_for(reading: PointReading) -> float | None:
        """Best available index for one point, or ``None`` if nothing usable."""
        if not reading.concentrations:
            return None
        try:
            return cpcb_aqi.compute(reading.concentrations).aqi
        except InsufficientDataError:
            pass
        # Fall back to a partial index. require_minimum=False is explicitly the
        # single-pollutant path, so this is a deliberate downgrade, not a bug.
        try:
            return cpcb_aqi.compute(reading.concentrations, require_minimum=False).aqi
        except InsufficientDataError:
            return None

    @staticmethod
    def is_national(readings: list[PointReading]) -> bool:
        """Whether every scored point met CPCB's minimum-pollutant rule."""
        for reading in readings:
            if not reading.concentrations:
                continue
            try:
                if not cpcb_aqi.compute(reading.concentrations).is_national:
                    return False
            except InsufficientDataError:
                return False
        return True

    @staticmethod
    def worst_confidence(readings: list[PointReading]) -> Confidence:
        """A route is only as trustworthy as its least-supported segment."""
        order = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
        if not readings:
            return Confidence.LOW
        return max((reading.confidence for reading in readings), key=order.__getitem__)

    _is_national = is_national
    _worst_confidence = worst_confidence

    def _windows(
        self,
        current_aqi: float,
        activity: Activity,
        climatology: ClimatologyProfile | None,
        forecast: ForecastProfile | None = None,
    ) -> tuple[HourWindow, ...]:
        """Recommended hours, from forecast when available, else climatology.

        A real forecast beats a climatology because it answers "what will the
        air be at 18:00" rather than "when is it usually cleaner". The basis
        field tells the client which one it got.
        """
        # Prefer forecast over climatology: it is a real prediction.
        if forecast is not None and not forecast.is_empty:
            windows: list[HourWindow] = []
            for hour, aqi in forecast.cleanest_hours(limit=3):
                windows.append(
                    HourWindow(
                        hour=hour,
                        mean_aqi=aqi,
                        category=categories.classify(aqi),
                        dose=engine.dose_for_duration(
                            aqi,
                            activity=activity,
                            duration_min=30.0,
                            hour=None,  # type: ignore[arg-type]
                        ),
                        samples=1,
                        basis="forecast",
                    )
                )
            return tuple(windows)

        if climatology is None or climatology.is_empty:
            return ()
        windows_clim: list[HourWindow] = []
        for hour, typical, samples in climatology.cleanest_hours(limit=3):
            windows_clim.append(
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
        return tuple(windows_clim)


def hours_for_parameter(pollutant: str) -> int | None:
    return AVERAGING_HOURS.get(pollutant)
