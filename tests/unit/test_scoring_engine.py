"""The exposure scoring engine.

This is the product. If these tests are wrong or thin, every number the app
shows is untrustworthy, so the suite covers determinism, the dose/mean split,
missing data, and the ranking tiebreak.
"""

from __future__ import annotations

import pytest

from aeropath.schemas.common import AQCategory, Activity, Advisory, RoadClass, Sensitivity
from aeropath.services.scoring import engine
from aeropath.services.scoring.engine import (
    DOSE_EQUIVALENCE_TOLERANCE,
    SegmentInput,
    dose_for_duration,
    rank_routes,
    score_route,
)
from aeropath.services.scoring.street_model import DEFAULT_MODEL


def segment(
    segment_id: int = 1,
    length_m: float = 100.0,
    base_aqi: float | None = 100.0,
    road_class: RoadClass = RoadClass.RESIDENTIAL,
    green_distance_m: float | None = None,
    water_distance_m: float | None = None,
) -> SegmentInput:
    return SegmentInput(
        segment_id=segment_id,
        length_m=length_m,
        base_aqi=base_aqi,
        road_class=road_class,
        green_distance_m=green_distance_m,
        water_distance_m=water_distance_m,
    )


def score(
    segments: list[SegmentInput],
    *,
    activity: Activity = Activity.WALK,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    hour: int = 14,
) -> engine.RouteScore:
    return score_route(
        segments,
        params=DEFAULT_MODEL,
        hour_of_day=hour,
        activity=activity,
        sensitivity=sensitivity,
    )


class TestMeanAndDose:
    def test_length_weighted_mean(self) -> None:
        """A long segment must dominate a short one, not count equally."""
        result = score([segment(1, 900.0, 100.0), segment(2, 100.0, 200.0)])
        assert result.mean_aqi == pytest.approx((100 * 900 + 200 * 100) / 1000)

    def test_dose_is_mean_times_length_times_activity(self) -> None:
        result = score([segment(1, 1000.0, 100.0)], activity=Activity.WALK)
        assert result.dose == pytest.approx(result.mean_aqi * 1000.0)

    def test_running_inhales_more(self) -> None:
        walk = score([segment(1, 1000.0, 100.0)], activity=Activity.WALK)
        run = score([segment(1, 1000.0, 100.0)], activity=Activity.RUN)
        assert run.dose > walk.dose
        assert run.mean_aqi == pytest.approx(walk.mean_aqi)

    def test_mean_and_dose_disagree_on_detours(self) -> None:
        """The reason both numbers exist.

        A long, gently polluted detour has the *lower mean* but the *higher
        total dose*. Ranking by mean would send the user on the dirtier walk.
        """
        short_dirty = score([segment(1, 1000.0, 200.0)])
        long_clean = score([segment(1, 4000.0, 120.0)])

        assert long_clean.mean_aqi < short_dirty.mean_aqi
        assert long_clean.dose > short_dirty.dose

        ranked = rank_routes([long_clean, short_dirty])
        assert ranked[0] is short_dirty

    def test_activity_factor_cannot_change_ranking(self) -> None:
        """activity_factor is a constant scalar, so it cancels in any ranking.

        Documented explicitly because FR6 reads as though activity tunes the
        scoring. It shifts absolute dose only; ranking is activity-invariant.
        """
        a = score([segment(1, 1000.0, 100.0)], activity=Activity.WALK)
        b = score([segment(1, 1000.0, 300.0)], activity=Activity.WALK)
        a_run = score([segment(1, 1000.0, 100.0)], activity=Activity.RUN)
        b_run = score([segment(1, 1000.0, 300.0)], activity=Activity.RUN)

        walk_order = [r.segments[0].base_aqi for r in rank_routes([a, b])]
        run_order = [r.segments[0].base_aqi for r in rank_routes([a_run, b_run])]
        assert walk_order == run_order == [100.0, 300.0]

    def test_empty_route_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            score([])

    def test_zero_length_route_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="scoreable length"):
            score([segment(1, 0.0, 100.0)])


class TestMissingData:
    def test_missing_cells_are_excluded_not_zeroed(self) -> None:
        """Treating a gap as clean air would favour routes with holes in the grid."""
        result = score([segment(1, 100.0, None), segment(2, 100.0, 300.0)])
        assert result.mean_aqi == pytest.approx(300.0)
        assert result.unscored_fraction == pytest.approx(0.5)

    def test_fully_missing_route_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="scoreable length"):
            score([segment(1, 100.0, None)])

    def test_unscored_fraction_is_reported(self) -> None:
        """Low coverage must be visible so the app can lower confidence."""
        result = score([segment(1, 900.0, None), segment(2, 100.0, 120.0)])
        assert result.unscored_fraction == pytest.approx(0.9)

    def test_complete_coverage_is_zero(self) -> None:
        assert score([segment(1, 100.0, 120.0)]).unscored_fraction == pytest.approx(0.0)


class TestRouteOutput:
    def test_category_matches_mean(self) -> None:
        result = score([segment(1, 1000.0, 150.0)])
        assert result.category is AQCategory.MODERATE

    def test_worst_segment_is_identified(self) -> None:
        result = score(
            [
                segment(1, 100.0, 50.0),
                segment(2, 100.0, 400.0, road_class=RoadClass.MOTORWAY),
                segment(3, 100.0, 60.0),
            ]
        )
        assert result.worst_segment_id == 2

    def test_advisory_follows_sensitivity_not_category(self) -> None:
        """The category is a fact about the air; the advisory is about the person."""
        normal = score([segment(1, 1000.0, 150.0)], sensitivity=Sensitivity.NORMAL)
        sensitive = score([segment(1, 1000.0, 150.0)], sensitivity=Sensitivity.HIGH)

        assert normal.category is sensitive.category is AQCategory.MODERATE
        assert normal.advisory is Advisory.CAUTION
        assert sensitive.advisory is Advisory.AVOID

    def test_segments_carry_their_factors(self) -> None:
        result = score([segment(1, 100.0, 100.0, green_distance_m=20.0)])
        factors = result.segments[0].factors
        assert "road" in factors and "time" in factors and "green" in factors

    def test_segment_categories_are_derived_from_adjusted_value(self) -> None:
        result = score([segment(1, 100.0, 40.0, road_class=RoadClass.MOTORWAY)], hour=9)
        seg = result.segments[0]
        assert seg.category.value != AQCategory.GOOD.value
        assert seg.adjusted_aqi > seg.base_aqi

    def test_magnitude_km(self) -> None:
        assert score([segment(1, 5200.0, 100.0)]).magnitude_km == pytest.approx(5.2)


class TestDeterminism:
    def test_identical_input_gives_identical_output(self) -> None:
        """Golden fixtures compare with a diff, so this must be exact."""
        segs = [
            segment(1, 120.0, 88.5, RoadClass.PRIMARY, 30.0, 400.0),
            segment(2, 340.0, 60.25, RoadClass.FOOTWAY, 10.0, None),
        ]
        first = score(segs, hour=9)
        second = score(segs, hour=9)
        assert first == second

    def test_segment_order_is_preserved(self) -> None:
        result = score([segment(7, 100.0, 100.0), segment(3, 100.0, 100.0)])
        assert [s.segment_id for s in result.segments] == [7, 3]


class TestRanking:
    def test_cleanest_first(self) -> None:
        dirty = score([segment(1, 1000.0, 300.0)])
        clean = score([segment(2, 1000.0, 50.0)])
        assert rank_routes([dirty, clean]) == [clean, dirty]

    def test_empty_ranking(self) -> None:
        assert rank_routes([]) == []

    def test_single_route(self) -> None:
        only = score([segment(1, 1000.0, 100.0)])
        assert rank_routes([only]) == [only]

    def test_near_equal_dose_prefers_the_shorter_route(self) -> None:
        """Within the tolerance the user cannot tell them apart, so walk less."""
        longer = score([segment(1, 20_000.0, 100.0)])
        shorter = score([segment(2, 5_000.0, 100.0)])
        ranked = rank_routes([longer, shorter])
        assert ranked[0] is shorter

    def test_clearly_cleaner_route_wins_even_if_longer(self) -> None:
        """The tiebreak must not become a length preference."""
        long_clean = score([segment(1, 8_000.0, 40.0)])
        short_dirty = score([segment(2, 5_000.0, 200.0)])
        ranked = rank_routes([short_dirty, long_clean])
        assert ranked[0] is long_clean

    def test_tolerance_boundary(self) -> None:
        base = score([segment(1, 10_000.0, 100.0)])
        just_inside = score([segment(2, 4_000.0, 100.0 * (1 + DOSE_EQUIVALENCE_TOLERANCE * 0.99))])
        assert rank_routes([base, just_inside])[0] is just_inside

    def test_sort_is_total_and_stable(self) -> None:
        """No route may be dropped or duplicated by the tiebreak pass."""
        routes = [score([segment(i, 1000.0 + i, 100.0 + i)]) for i in range(1, 8)]
        ranked = rank_routes(routes)
        assert len(ranked) == len(routes)
        assert {id(r) for r in ranked} == {id(r) for r in routes}


class TestWindowDose:
    def test_matches_route_dose_units(self) -> None:
        route = score([segment(1, 1000.0, 100.0)], activity=Activity.WALK)
        window = dose_for_duration(100.0, activity=Activity.WALK, duration_min=60.0, hour=None)  # type: ignore[arg-type]
        assert window == pytest.approx(100.0 * 60.0 * 1.0)
        assert route.dose == pytest.approx(100_000.0)

    def test_running_accumulates_faster(self) -> None:
        walk = dose_for_duration(100.0, activity=Activity.WALK, duration_min=30.0, hour=None)  # type: ignore[arg-type]
        run = dose_for_duration(100.0, activity=Activity.RUN, duration_min=30.0, hour=None)  # type: ignore[arg-type]
        assert run == pytest.approx(walk * 3)

    def test_rejects_non_positive_duration(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            dose_for_duration(100.0, activity=Activity.WALK, duration_min=0.0, hour=None)  # type: ignore[arg-type]
