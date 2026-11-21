"""Circular loop route generator.

Generates scenic, closed-circuit walking or running loops starting and ending at
the same origin point matching a target distance, while minimizing air pollution exposure.
"""

from __future__ import annotations

import contextlib
import logging
import math
from datetime import UTC, datetime

from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.external.osrm import OsrmRouter, RoutingError
from aeropath.external.provider import GridProvider
from aeropath.schemas.common import Coordinate
from aeropath.schemas.loop import LoopRequest
from aeropath.services import routing
from aeropath.services.planning import PlannedRoute, PlanService
from aeropath.services.scoring import engine, street_model

logger = logging.getLogger(__name__)


class LoopGenerator:
    """Generates and scores closed loops around an origin."""

    def __init__(self, provider: GridProvider, router: OsrmRouter) -> None:
        self._provider = provider
        self._router = router

    async def generate_loops(
        self,
        request: LoopRequest,
        at: datetime | None = None,
    ) -> list[PlannedRoute]:
        """Generate candidate loops around the origin and rank by lowest exposure."""
        moment = at or datetime.now(UTC)
        target_m = request.target_distance_km * 1000.0

        # Approximate circular radius accounting for street tortuosity (~1.25)
        radius_m = target_m / (2.0 * math.pi * 1.25)

        # Convert meters to approximate lat/lng deltas
        d_lat = radius_m / 111_000.0
        cos_lat = math.cos(math.radians(request.origin.lat))
        d_lng = radius_m / (111_000.0 * max(0.1, abs(cos_lat)))

        headings = [0.0, 90.0, 180.0, 270.0]
        profile = routing.activity_profile(request.activity)

        planned_loops: list[PlannedRoute] = []

        for base_heading in headings:
            angle1 = math.radians(base_heading - 45.0)
            angle2 = math.radians(base_heading + 45.0)

            wp1 = Coordinate(
                lat=request.origin.lat + d_lat * math.cos(angle1),
                lng=request.origin.lng + d_lng * math.sin(angle1),
            )
            wp2 = Coordinate(
                lat=request.origin.lat + d_lat * math.cos(angle2),
                lng=request.origin.lng + d_lng * math.sin(angle2),
            )

            waypoints = (request.origin, wp1, wp2, request.origin)

            try:
                geometries = await self._router.route_waypoints(waypoints, profile=profile)
            except (RoutingError, ValueError) as exc:
                logger.debug("Loop routing failed for heading %s: %s", base_heading, exc)
                continue

            for geometry in geometries:
                if abs(geometry.distance_m - target_m) / target_m > 0.40:
                    continue

                segments = routing.segments_from_geometry(geometry)
                if not segments:
                    continue

                readings = await self._provider.concentrations_at(
                    [segment.coordinate for segment in segments], at=moment
                )

                engine_segments: list[engine.SegmentInput] = []
                for seg, reading in zip(segments, readings, strict=True):
                    base_aqi = None
                    if reading.concentrations:
                        with contextlib.suppress(InsufficientDataError):
                            base_aqi = cpcb_aqi.compute(
                                reading.concentrations, require_minimum=False
                            ).aqi
                    engine_segments.append(
                        engine.SegmentInput(
                            segment_id=seg.segment_id,
                            length_m=seg.length_m,
                            base_aqi=base_aqi,
                            road_class=seg.road_class,
                        )
                    )

                try:
                    score = engine.score_route(
                        engine_segments,
                        params=street_model.DEFAULT_MODEL,
                        hour_of_day=moment.hour,
                        activity=request.activity,
                        sensitivity=request.sensitivity,
                    )
                except ValueError:
                    continue

                planned_loops.append(
                    PlannedRoute(
                        distance_m=geometry.distance_m,
                        duration_s=geometry.duration_s,
                        score=score,
                        aqi_confidence=PlanService.worst_confidence(readings),
                        basis="measured",
                        geometry=geometry.coordinates,
                        is_national_aqi=PlanService.is_national(readings),
                        pollutants_used=max(
                            (len(r.concentrations) for r in readings),
                            default=0,
                        ),
                    )
                )

        if not planned_loops:
            return []

        ranked_scores = engine.rank_routes([loop.score for loop in planned_loops])
        ordered_by_id = {id(loop.score): loop for loop in planned_loops}
        ranked = [ordered_by_id[id(s)] for s in ranked_scores]
        return ranked[: request.num_alternatives]
