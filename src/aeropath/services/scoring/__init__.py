"""Deterministic scoring: the product's core."""

from aeropath.services.scoring.engine import (
    ACTIVITY_FACTORS,
    DOSE_EQUIVALENCE_TOLERANCE,
    RouteScore,
    SegmentInput,
    SegmentScore,
    dose_for_duration,
    rank_routes,
    score_route,
)
from aeropath.services.scoring.street_model import (
    DEFAULT_MODEL,
    StreetModelParams,
    adjust,
    local_hour,
)

__all__ = [
    "ACTIVITY_FACTORS",
    "DEFAULT_MODEL",
    "DOSE_EQUIVALENCE_TOLERANCE",
    "RouteScore",
    "SegmentInput",
    "SegmentScore",
    "StreetModelParams",
    "adjust",
    "dose_for_duration",
    "local_hour",
    "rank_routes",
    "score_route",
]
