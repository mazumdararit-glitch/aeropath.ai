"""Accuracy metrics and the Phase 0 gate verdict."""

from __future__ import annotations

import pytest

from aeropath.domain.metrics import (
    REFERENCE_FLOOR,
    CategoryAgreement,
    GateResult,
    Pair,
    category_agreement,
    error_stats,
    relative_improvement,
)
from aeropath.schemas.common import AQCategory


class TestErrorStats:
    def test_perfect_prediction(self) -> None:
        stats = error_stats("perfect", [Pair(100.0, 100.0), Pair(50.0, 50.0)])
        assert stats.mape == pytest.approx(0.0)
        assert stats.bias == pytest.approx(0.0)
        assert stats.rmse == pytest.approx(0.0)

    def test_known_mape(self) -> None:
        # |110-100|/100 = 10%, |90-100|/100 = 10% -> mean 10%
        stats = error_stats("known", [Pair(110.0, 100.0), Pair(90.0, 100.0)])
        assert stats.mape == pytest.approx(10.0)
        assert stats.bias == pytest.approx(0.0)

    def test_bias_has_direction(self) -> None:
        """Over-predicting pollution and under-predicting it must not cancel."""
        high = error_stats("high", [Pair(150.0, 100.0)])
        low = error_stats("low", [Pair(50.0, 100.0)])
        assert high.bias > 0
        assert low.bias < 0

    def test_rmse_punishes_large_errors(self) -> None:
        spread = error_stats("spread", [Pair(200.0, 100.0), Pair(100.0, 100.0)])
        flat = error_stats("flat", [Pair(120.0, 100.0), Pair(120.0, 100.0)])
        assert spread.rmse > flat.rmse

    def test_near_zero_references_are_excluded(self) -> None:
        """A reference of 1 µg/m³ makes percentage error meaningless."""
        stats = error_stats("tiny", [Pair(5.0, 1.0), Pair(100.0, 100.0)])
        assert stats.n == 1
        assert stats.excluded == 1

    def test_floor_is_respected(self) -> None:
        stats = error_stats("floor", [Pair(REFERENCE_FLOOR, REFERENCE_FLOOR)])
        assert stats.n == 1

    def test_empty_is_flagged(self) -> None:
        stats = error_stats("empty", [])
        assert stats.is_empty
        assert stats.n == 0


class TestRelativeImprovement:
    def test_positive_when_better(self) -> None:
        baseline = error_stats("raw", [Pair(150.0, 100.0)])
        candidate = error_stats("adj", [Pair(110.0, 100.0)])
        assert relative_improvement(baseline, candidate) == pytest.approx(80.0)

    def test_negative_when_worse(self) -> None:
        baseline = error_stats("raw", [Pair(110.0, 100.0)])
        candidate = error_stats("adj", [Pair(130.0, 100.0)])
        assert relative_improvement(baseline, candidate) < 0

    def test_zero_for_no_change(self) -> None:
        same = error_stats("same", [Pair(120.0, 100.0)])
        assert relative_improvement(same, same) == pytest.approx(0.0)

    def test_zero_when_no_samples(self) -> None:
        """No improvement may be claimed from an empty comparison."""
        empty = error_stats("empty", [])
        assert relative_improvement(empty, error_stats("x", [Pair(10.0, 10.0)])) == 0.0


class TestCategoryAgreement:
    def test_all_agree(self) -> None:
        result = category_agreement([(40.0, 45.0), (150.0, 160.0)])
        assert result.total == 2
        assert result.rate == pytest.approx(1.0)

    def test_none_agree(self) -> None:
        result = category_agreement([(40.0, 400.0)])
        assert result.rate == pytest.approx(0.0)

    def test_confusion_shape(self) -> None:
        result = category_agreement([(40.0, 45.0), (40.0, 150.0)])
        assert result.confusion[(AQCategory.GOOD, AQCategory.GOOD)] == 1
        assert result.confusion[(AQCategory.GOOD, AQCategory.MODERATE)] == 1

    def test_empty(self) -> None:
        result = category_agreement([])
        assert result.rate == 0.0
        assert isinstance(result, CategoryAgreement)

    def test_worst_confusions_rank_two_band_misses_first(self) -> None:
        """Saying "Good" when it is "Very Poor" is the failure that loses trust."""
        result = category_agreement(
            [
                (40.0, 150.0),  # Good vs Moderate: 1 band
                (40.0, 350.0),  # Good vs Very Poor: 3 bands
                (40.0, 150.0),
            ]
        )
        worst = result.worst_confusions
        assert worst[0][0] is AQCategory.GOOD
        assert worst[0][1] is AQCategory.VERY_POOR


class TestGate:
    def test_passes_when_both_conditions_hold(self) -> None:
        gate = GateResult(
            category_agreement=0.78,
            raw_category_agreement=0.66,
            adjusted_mape=18.0,
            raw_mape=25.0,
            samples=500,
        )
        assert gate.passed
        assert gate.verdict() == "PASS"

    def test_fails_on_category_even_with_better_mape(self) -> None:
        """A more accurate number the user still sees as the wrong colour fails."""
        gate = GateResult(
            category_agreement=0.60,
            raw_category_agreement=0.66,
            adjusted_mape=10.0,
            raw_mape=25.0,
            samples=500,
        )
        assert not gate.passed
        assert "category agreement gain" in gate.verdict()

    def test_fails_without_beating_raw(self) -> None:
        """A model that is merely different cannot ship as if it were better."""
        gate = GateResult(
            category_agreement=0.90,
            raw_category_agreement=0.66,
            adjusted_mape=24.0,
            raw_mape=25.0,
            samples=500,
        )
        assert not gate.passed
        assert "MAPE improvement" in gate.verdict()

    def test_category_gate_is_relative_to_baseline(self) -> None:
        """The old absolute 70% target failed models the baseline could not hit.

        A raw baseline of 66% with an adjusted 71% is a 5-point gain and must
        pass, even though it is below the old absolute 70%... which it clears.
        The real regression guard is the reverse: 69% against a 66% baseline is
        a 3-point gain and must fail despite being near that absolute number.
        """
        near_absolute = GateResult(
            category_agreement=0.69,
            raw_category_agreement=0.66,
            adjusted_mape=20.0,
            raw_mape=25.0,
            samples=500,
        )
        assert near_absolute.category_gain == pytest.approx(0.03)
        assert not near_absolute.passed
        assert "category agreement gain" in near_absolute.verdict()

    def test_low_baseline_is_inconclusive_not_pass(self) -> None:
        """Beating a useless baseline proves nothing, however large the gain."""
        gate = GateResult(
            category_agreement=0.45,
            raw_category_agreement=0.20,
            adjusted_mape=10.0,
            raw_mape=40.0,
            samples=500,
        )
        assert not gate.passed
        assert "INCONCLUSIVE" in gate.verdict()
        assert "baseline" in gate.verdict()

    def test_improvement_arithmetic(self) -> None:
        gate = GateResult(
            category_agreement=0.9,
            raw_category_agreement=0.6,
            adjusted_mape=20.0,
            raw_mape=25.0,
        )
        assert gate.improvement == pytest.approx(20.0)

    def test_no_samples_is_inconclusive(self) -> None:
        gate = GateResult(
            category_agreement=0.0,
            raw_category_agreement=0.0,
            adjusted_mape=0.0,
            raw_mape=0.0,
        )
        assert not gate.passed
        assert "INCONCLUSIVE" in gate.verdict()

    def test_both_failures_reported(self) -> None:
        gate = GateResult(
            category_agreement=0.10,
            raw_category_agreement=0.66,
            adjusted_mape=99.0,
            raw_mape=25.0,
            samples=10,
        )
        verdict = gate.verdict()
        assert "category agreement" in verdict
        assert "MAPE improvement" in verdict
