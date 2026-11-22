"""Statistics behind the road-class residual analysis.

These decide whether ``road_factor`` is worth refitting at all, so the failure
modes that matter are (a) treating autocorrelated hourly samples as independent
and (b) reading a large eta-squared as signal when class count alone inflates it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from aeropath.schemas.common import Coordinate, RoadClass

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from road_class_residuals import (  # noqa: E402
    COLOCATED_KM,
    bootstrap_mean_ci,
    dedupe_colocated,
    eta_squared,
    eta_squared_p_value,
    permutation_p_value,
    station_residuals,
)


class _Station:
    def __init__(self, station_id: str, lat: float, lng: float) -> None:
        self.station_id = station_id
        self.coordinate = Coordinate(lat=lat, lng=lng)
        self.name = station_id


class TestDedupeColocated:
    def test_collapses_identical_coordinates(self) -> None:
        """Delhi publishes Lodhi Road and Pusa twice, at 0 m apart."""
        stations = [
            _Station("a", 28.5900, 77.2200),
            _Station("b", 28.5900, 77.2200),
            _Station("c", 28.7000, 77.2000),
        ]
        kept, dropped = dedupe_colocated(stations)
        assert [s.station_id for s in kept] == ["a", "c"]
        assert dropped == [("b", "a", 0.0)]

    def test_keeps_stations_beyond_the_threshold(self) -> None:
        # ~0.2 km apart: distinct monitors, not duplicates.
        stations = [_Station("a", 28.5900, 77.2200), _Station("b", 28.5918, 77.2200)]
        kept, dropped = dedupe_colocated(stations)
        assert len(kept) == 2
        assert dropped == []

    def test_threshold_is_metres_not_kilometres(self) -> None:
        assert COLOCATED_KM < 1.0

    def test_is_order_independent_in_output(self) -> None:
        forward = [_Station("a", 28.59, 77.22), _Station("b", 28.59, 77.22)]
        backward = list(reversed(forward))
        kept_forward, _ = dedupe_colocated(forward)
        kept_backward, _ = dedupe_colocated(backward)
        assert [s.station_id for s in kept_forward] == [s.station_id for s in kept_backward]


class TestStationResiduals:
    def test_colocated_pair_does_not_divide_by_zero(self) -> None:
        """The regression: two stations at the same point crashed the run."""
        coords = {
            "a": Coordinate(lat=28.59, lng=77.22),
            "b": Coordinate(lat=28.59, lng=77.22),
            "c": Coordinate(lat=28.70, lng=77.20),
        }
        truth = {
            ("a", datetime(2024, 11, 1, 0)): 100.0,
            ("b", datetime(2024, 11, 1, 0)): 120.0,
            ("c", datetime(2024, 11, 1, 0)): 90.0,
        }
        residuals = station_residuals(truth, coords)
        assert set(residuals) == {"a", "b", "c"}
        for values in residuals.values():
            assert all(value == value for value in values)  # not NaN

    def test_perfect_neighbours_give_zero_residual(self) -> None:
        coords = {
            "a": Coordinate(lat=28.60, lng=77.20),
            "b": Coordinate(lat=28.70, lng=77.20),
            "c": Coordinate(lat=28.80, lng=77.20),
        }
        truth = {
            ("a", datetime(2024, 11, 1, 0)): 100.0,
            ("b", datetime(2024, 11, 1, 0)): 100.0,
            ("c", datetime(2024, 11, 1, 0)): 100.0,
        }
        residuals = station_residuals(truth, coords)
        assert residuals["b"][0] == pytest.approx(0.0)

    def test_dirty_station_has_positive_residual(self) -> None:
        coords = {
            "a": Coordinate(lat=28.60, lng=77.20),
            "dirty": Coordinate(lat=28.70, lng=77.20),
            "c": Coordinate(lat=28.80, lng=77.20),
        }
        truth = {
            ("a", datetime(2024, 11, 1, 0)): 100.0,
            ("dirty", datetime(2024, 11, 1, 0)): 150.0,
            ("c", datetime(2024, 11, 1, 0)): 100.0,
        }
        residuals = station_residuals(truth, coords)
        assert residuals["dirty"][0] > 0


class TestEtaSquared:
    def test_no_difference_between_groups_is_zero(self) -> None:
        groups = {
            RoadClass.SECONDARY: [0.1, 0.1, 0.1],
            RoadClass.RESIDENTIAL: [0.1, 0.1, 0.1],
        }
        assert eta_squared(groups) == pytest.approx(0.0)

    def test_perfect_separation_is_one(self) -> None:
        groups = {
            RoadClass.SECONDARY: [-0.2, -0.2, -0.2],
            RoadClass.RESIDENTIAL: [0.2, 0.2, 0.2],
        }
        assert eta_squared(groups) == pytest.approx(1.0)

    def test_within_group_spread_reduces_explained_variance(self) -> None:
        """The point of the metric: a class spanning 55 points explains little."""
        tight = {
            RoadClass.SECONDARY: [-0.2, -0.2],
            RoadClass.RESIDENTIAL: [0.2, 0.2],
        }
        noisy = {
            RoadClass.SECONDARY: [-0.4, 0.0],
            RoadClass.RESIDENTIAL: [0.0, 0.4],
        }
        assert eta_squared(tight) > eta_squared(noisy)

    def test_permutation_test_finds_signal_when_separated(self) -> None:
        groups = {
            RoadClass.SECONDARY: [-0.30, -0.28, -0.26],
            RoadClass.RESIDENTIAL: [0.26, 0.28, 0.30],
            RoadClass.TERTIARY: [-0.01, 0.0, 0.01],
        }
        assert eta_squared_p_value(groups, draws=500) < 0.05

    def test_permutation_test_rejects_noise(self) -> None:
        """Six classes over 12 stations inflates eta-squared; the test must not care."""
        groups = {
            RoadClass.SECONDARY: [0.01, -0.01],
            RoadClass.RESIDENTIAL: [0.02, -0.02],
            RoadClass.TERTIARY: [0.015, -0.015],
            RoadClass.SERVICE: [0.012, -0.012],
            RoadClass.PEDESTRIAN: [0.018, -0.018],
            RoadClass.FOOTWAY: [0.013, -0.013],
        }
        assert eta_squared_p_value(groups, draws=500) > 0.05


class TestPairwiseAndBootstrap:
    def test_permutation_detects_large_difference(self) -> None:
        p = permutation_p_value(
            [-0.30, -0.28, -0.31, -0.29, -0.27],
            [0.30, 0.29, 0.32, 0.31, 0.28],
            draws=2000,
        )
        assert p is not None and p < 0.05

    def test_small_groups_cannot_reach_significance(self) -> None:
        """A property of the test, not a bug: with 3 v 3 the floor is ~0.10.

        This is why Kolkata's 1-station-per-class network can never produce a
        significant road-class result, however large the raw difference looks.
        """
        p = permutation_p_value([-0.3, -0.28, -0.31], [0.3, 0.29, 0.32], draws=2000)
        assert p is not None and p >= 0.05

    def test_permutation_needs_two_per_group(self) -> None:
        assert permutation_p_value([0.1], [0.2, 0.3]) is None

    def test_bootstrap_ci_brackets_the_mean(self) -> None:
        values = [-0.1, 0.0, 0.1, 0.05, -0.05]
        ci = bootstrap_mean_ci(values, draws=500)
        assert ci is not None
        assert ci[0] <= sum(values) / len(values) <= ci[1]

    def test_bootstrap_needs_two_values(self) -> None:
        assert bootstrap_mean_ci([0.1], draws=100) is None

    def test_bootstrap_is_deterministic(self) -> None:
        values = [-0.1, 0.0, 0.1, 0.2]
        assert bootstrap_mean_ci(values, draws=300) == bootstrap_mean_ci(values, draws=300)
