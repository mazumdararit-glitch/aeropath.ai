"""Tests for candidate generation, Pareto pruning, and 3 labeled routes (Sprint 2 - R2)."""

from __future__ import annotations

from aeropath.schemas.common import Activity, Advisory, AQCategory
from aeropath.services.candidate_router import (
    CandidateRoute,
    filter_pareto_non_dominated,
    label_three_routes,
)
from aeropath.services.scoring import engine


def _dummy_score(dose: float, mean_aqi: float = 50.0) -> engine.RouteScore:
    return engine.RouteScore(
        segments=(),
        distance_m=1000.0,
        mean_aqi=mean_aqi,
        dose=dose,
        category=AQCategory.MODERATE,
        activity=Activity.WALK,
        advisory=Advisory.OK,
        worst_segment_id=None,
        unscored_fraction=0.0,
    )


def test_pareto_pruning_removes_dominated_candidates() -> None:
    # Route A: duration 600s, dose 30.0 (Clean & Fast)
    # Route B: duration 700s, dose 40.0 (Slower AND Dirtier than A -> DOMINATED)
    # Route C: duration 500s, dose 50.0 (Faster than A, but higher dose -> NON-DOMINATED)
    candidates = [
        CandidateRoute(distance_m=1000.0, duration_s=600.0, dose=30.0, mean_aqi=60.0),
        CandidateRoute(distance_m=1100.0, duration_s=700.0, dose=40.0, mean_aqi=80.0),
        CandidateRoute(distance_m=900.0, duration_s=500.0, dose=50.0, mean_aqi=100.0),
    ]

    non_dominated = filter_pareto_non_dominated(candidates)
    assert len(non_dominated) == 2
    doses = {c.dose for c in non_dominated}
    assert doses == {30.0, 50.0}


def test_label_three_routes_direct_clean_balanced() -> None:
    # Direct (fastest): 500s, dose 100.0, dist 1000m
    direct = CandidateRoute(distance_m=1000.0, duration_s=500.0, dose=100.0, mean_aqi=150.0)
    # Max Clean Air: 580s, dose 50.0 (50% less dose!), dist 1160m (16% detour <= 20% cap)
    clean = CandidateRoute(distance_m=1160.0, duration_s=580.0, dose=50.0, mean_aqi=50.0)
    # Balanced: 530s (+30s), dose 75.0 (25% less dose!), dist 1060m (6% detour)
    balanced = CandidateRoute(distance_m=1060.0, duration_s=530.0, dose=75.0, mean_aqi=90.0)

    labeled = label_three_routes(
        [direct, clean, balanced],
        max_detour_pct=20.0,
        balanced_min_reduction_pct=15.0,
        balanced_max_dist_pct=10.0,
    )

    labels = {r.route_label for r in labeled}
    assert "Direct" in labels
    assert "Max Clean Air" in labels
    assert "Balanced" in labels


def test_detour_cap_excludes_excessive_detours() -> None:
    # Direct: 1000m
    direct = CandidateRoute(distance_m=1000.0, duration_s=500.0, dose=100.0, mean_aqi=150.0)
    # Super clean, but 30% detour (> 20% cap)
    excessive = CandidateRoute(distance_m=1300.0, duration_s=650.0, dose=20.0, mean_aqi=30.0)
    # Moderate clean within 20% cap: 1150m (15% detour)
    valid_clean = CandidateRoute(distance_m=1150.0, duration_s=580.0, dose=60.0, mean_aqi=70.0)

    labeled = label_three_routes(
        [direct, excessive, valid_clean],
        max_detour_pct=20.0,
        balanced_min_reduction_pct=15.0,
        balanced_max_dist_pct=10.0,
    )

    clean_route = next(r for r in labeled if r.route_label == "Max Clean Air")
    assert clean_route.distance_m == 1150.0  # Excessive detour was rejected for Max Clean Air label


def test_balanced_label_skipped_when_none_qualifies() -> None:
    # Direct
    direct = CandidateRoute(distance_m=1000.0, duration_s=500.0, dose=100.0, mean_aqi=150.0)
    # Clean air route (20% detour, 30% less dose)
    clean = CandidateRoute(distance_m=1180.0, duration_s=600.0, dose=70.0, mean_aqi=70.0)
    # Another route that is 8% longer, but only 3% less dose (fails 15% dose reduction threshold)
    not_balanced = CandidateRoute(distance_m=1080.0, duration_s=540.0, dose=97.0, mean_aqi=140.0)

    labeled = label_three_routes(
        [direct, clean, not_balanced],
        max_detour_pct=20.0,
        balanced_min_reduction_pct=15.0,
        balanced_max_dist_pct=10.0,
    )

    labels = {r.route_label for r in labeled}
    assert "Direct" in labels
    assert "Max Clean Air" in labels
    assert "Balanced" not in labels
