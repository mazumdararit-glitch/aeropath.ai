"""CPCB rolling windows.

A national AQI is not computed from a single hourly sample: PM, NO2 and SO2 use
24-hour means and CO and O3 use 8-hour means. These tests pin the window
arithmetic, because a 24-hour mean computed over 3 hours would quietly report
the current spike as the day's air quality.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.domain.national_aqi import (
    AVERAGING_HOURS,
    hourly_mean_series,
    rolling_aqi,
    window_stats,
)

BASE = datetime(2024, 11, 1, 0, 0)


def _series(hours: int, value: float, *, pollutant: str = "PM2.5") -> dict[datetime, float]:
    return {BASE + timedelta(hours=i): value for i in range(hours)}


def _all_pollutants(hours: int) -> dict[str, dict[datetime, float]]:
    return {
        "PM2.5": _series(hours, 60.0),
        "PM10": _series(hours, 100.0),
        "NO2": _series(hours, 40.0),
        "SO2": _series(hours, 20.0),
        "CO": _series(hours, 1.0),
        "Ozone": _series(hours, 50.0),
    }


class TestAveragingPeriods:
    def test_cpcb_periods_are_encoded(self) -> None:
        assert AVERAGING_HOURS["PM2.5"] == 24
        assert AVERAGING_HOURS["PM10"] == 24
        assert AVERAGING_HOURS["NO2"] == 24
        assert AVERAGING_HOURS["SO2"] == 24
        assert AVERAGING_HOURS["CO"] == 8
        assert AVERAGING_HOURS["Ozone"] == 8

    def test_particulates_use_24_hours_not_8(self) -> None:
        assert AVERAGING_HOURS["PM2.5"] != AVERAGING_HOURS["CO"]


class TestRollingAqi:
    def test_computes_when_windows_are_complete(self) -> None:
        result = rolling_aqi(_all_pollutants(48), at=BASE + timedelta(hours=47))
        assert result.aqi > 0
        assert result.pollutant_count == 6

    def test_spike_is_averaged_away_by_the_window(self) -> None:
        """One bad hour must not become the reported daily AQI."""
        series = _all_pollutants(48)
        series["PM2.5"] = _series(48, 60.0)
        series["PM2.5"][BASE + timedelta(hours=47)] = 500.0

        result = rolling_aqi(series, at=BASE + timedelta(hours=47))
        # The mean is dominated by the 47 clean hours, so PM2.5 stays Moderate.
        pm25 = next(s for s in result.sub_indices if s.pollutant == "PM2.5")
        assert pm25.concentration < 120.0

    def test_uses_only_the_window_ending_at_at(self) -> None:
        series = _all_pollutants(48)
        # Old hours at a wild value must not leak into a later window.
        for hour in range(0, 10):
            series["PM2.5"][BASE + timedelta(hours=hour)] = 999.0

        result = rolling_aqi(series, at=BASE + timedelta(hours=47))
        pm25 = next(s for s in result.sub_indices if s.pollutant == "PM2.5")
        assert pm25.concentration < 200.0

    def test_insufficient_pollutants_raises(self) -> None:
        with pytest.raises(InsufficientDataError, match="needs 3"):
            rolling_aqi({"PM2.5": _series(48, 60.0)}, at=BASE + timedelta(hours=47))

    def test_requires_a_particulate(self) -> None:
        only_gases = {
            "NO2": _series(48, 40.0),
            "SO2": _series(48, 20.0),
            "CO": _series(48, 1.0),
        }
        with pytest.raises(InsufficientDataError, match=r"PM2\.5 or PM10"):
            rolling_aqi(only_gases, at=BASE + timedelta(hours=47))

    def test_short_window_is_excluded_not_shortened(self) -> None:
        """A 5-of-24-hour mean is not a 24-hour mean."""
        series = _all_pollutants(48)
        # Five hours ending at the evaluation time: inside the window, but only
        # 5/24 = 21% covered, which is below the default 75%.
        series["PM2.5"] = {BASE + timedelta(hours=h): 60.0 for h in range(43, 48)}

        result = rolling_aqi(series, at=BASE + timedelta(hours=47))
        assert all(s.pollutant != "PM2.5" for s in result.sub_indices)

    def test_min_coverage_is_configurable(self) -> None:
        series = _all_pollutants(48)
        series["PM2.5"] = {BASE + timedelta(hours=h): 60.0 for h in range(43, 48)}

        result = rolling_aqi(series, at=BASE + timedelta(hours=47), min_coverage=0.2)
        assert any(s.pollutant == "PM2.5" for s in result.sub_indices)


class TestWindowStats:
    def test_reports_coverage_per_pollutant(self) -> None:
        stats = window_stats(_all_pollutants(48), at=BASE + timedelta(hours=47))
        assert stats.hours_required["PM2.5"] == 24
        assert stats.hours_required["CO"] == 8
        assert stats.coverage("PM2.5") == pytest.approx(1.0)


class TestHourlyMeanSeries:
    def test_averages_multiple_readings_in_one_hour(self) -> None:
        readings = [
            ("PM2.5", BASE, 60.0),
            ("PM2.5", BASE, 80.0),
            ("PM2.5", BASE + timedelta(hours=1), 100.0),
        ]
        series = hourly_mean_series(readings)
        assert series["PM2.5"][BASE] == pytest.approx(70.0)
        assert series["PM2.5"][BASE + timedelta(hours=1)] == pytest.approx(100.0)

    def test_keeps_pollutants_separate(self) -> None:
        readings = [("PM2.5", BASE, 60.0), ("CO", BASE, 1.0)]
        series = hourly_mean_series(readings)
        assert set(series) == {"PM2.5", "CO"}
