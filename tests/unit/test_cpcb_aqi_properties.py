"""Property-based verification of CPCB AQI sub-index mathematical invariants.

Invariants verified:
1. Monotonicity: c1 <= c2 ==> sub_index(p, c1) <= sub_index(p, c2).
2. Continuity: limit at each breakpoint boundary is continuous within numerical epsilon.
3. Boundedness: 0 <= sub_index(p, c) <= 500 for c in [0, max_breakpoint].
4. Non-negativity and strictly zero at zero concentration.
"""

from __future__ import annotations

import pytest

from aeropath.domain.cpcb_aqi import _ANCHORS, sub_index

POLLUTANTS = list(_ANCHORS.keys())


class TestSubIndexProperties:
    @pytest.mark.parametrize("pollutant", POLLUTANTS)
    def test_zero_concentration_is_zero(self, pollutant: str) -> None:
        """sub_index(0) must evaluate to exactly 0.0."""
        assert sub_index(pollutant, 0.0) == 0.0

    @pytest.mark.parametrize("pollutant", POLLUTANTS)
    def test_monotonicity_across_domain(self, pollutant: str) -> None:
        """sub_index must be strictly non-decreasing across 1000 domain sample points."""
        anchors = _ANCHORS[pollutant]
        max_c = anchors[-1][0]
        step = max_c / 1000.0
        prev_idx = 0.0

        for i in range(1001):
            c = i * step
            idx = sub_index(pollutant, c)
            assert idx >= prev_idx, (
                f"Monotonicity violated for {pollutant} at {c}: {idx} < {prev_idx}"
            )
            assert 0.0 <= idx <= 500.0, f"Boundedness violated for {pollutant} at {c}: {idx}"
            prev_idx = idx

    @pytest.mark.parametrize("pollutant", POLLUTANTS)
    def test_continuity_at_breakpoints(self, pollutant: str) -> None:
        """sub_index must be continuous at each internal breakpoint boundary."""
        anchors = _ANCHORS[pollutant]
        eps = 1e-6
        for c_bp, expected_aqi in anchors[1:-1]:
            left_val = sub_index(pollutant, c_bp - eps)
            at_val = sub_index(pollutant, c_bp)
            right_val = sub_index(pollutant, c_bp + eps)

            assert abs(at_val - expected_aqi) < 1e-4, (
                f"Breakpoint value {at_val} != expected {expected_aqi} for {pollutant} at {c_bp}"
            )
            assert abs(left_val - at_val) < 1e-3, (
                f"Left discontinuity for {pollutant} at {c_bp}: |{left_val} - {at_val}| >= 1e-3"
            )
            assert abs(right_val - at_val) < 1e-3, (
                f"Right discontinuity for {pollutant} at {c_bp}: |{right_val} - {at_val}| >= 1e-3"
            )
