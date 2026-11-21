"""CPCB AQI computation from concentrations.

This module is the ground truth for the Phase 0 gate, so every published band
edge is asserted exactly rather than approximately.
"""

from __future__ import annotations

import pytest

from aeropath.domain.cpcb_aqi import (
    MINIMUM_POLLUTANTS,
    InsufficientDataError,
    canonical_pollutant,
    compute,
    sub_index,
    try_compute,
)
from aeropath.schemas.common import AQCategory


class TestSubIndexEdges:
    @pytest.mark.parametrize(
        ("concentration", "expected"),
        [
            (0.0, 0.0),
            (15.0, 25.0),
            (30.0, 50.0),
            (45.0, 75.0),
            (60.0, 100.0),
            (75.0, 150.0),
            (90.0, 200.0),
            (105.0, 250.0),
            (120.0, 300.0),
            (185.0, 350.0),
            (250.0, 400.0),
            (375.0, 450.0),
            (500.0, 500.0),
        ],
    )
    def test_pm25_anchors_and_midpoints(self, concentration: float, expected: float) -> None:
        assert sub_index("PM2.5", concentration) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("concentration", "expected"),
        [
            (0.0, 0.0),
            (50.0, 50.0),
            (100.0, 100.0),
            (250.0, 200.0),
            (350.0, 300.0),
            (430.0, 400.0),
            (600.0, 500.0),
        ],
    )
    def test_pm10_anchors(self, concentration: float, expected: float) -> None:
        assert sub_index("PM10", concentration) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("concentration", "expected"),
        [
            (0.0, 0.0),
            (40.0, 50.0),
            (80.0, 100.0),
            (180.0, 200.0),
            (280.0, 300.0),
            (400.0, 400.0),
        ],
    )
    def test_no2_anchors(self, concentration: float, expected: float) -> None:
        assert sub_index("NO2", concentration) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("concentration", "expected"),
        [(0.0, 0.0), (1.0, 50.0), (2.0, 100.0), (10.0, 200.0), (17.0, 300.0), (34.0, 400.0)],
    )
    def test_co_anchors(self, concentration: float, expected: float) -> None:
        assert sub_index("CO", concentration) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("concentration", "expected"),
        [(0.0, 0.0), (50.0, 50.0), (100.0, 100.0), (168.0, 200.0), (208.0, 300.0), (748.0, 400.0)],
    )
    def test_ozone_anchors(self, concentration: float, expected: float) -> None:
        assert sub_index("Ozone", concentration) == pytest.approx(expected)


class TestSubIndexBehaviour:
    def test_saturates_above_scale(self) -> None:
        assert sub_index("PM2.5", 5000.0) == 500.0

    def test_monotonic_across_every_pollutant(self) -> None:
        for pollutant, top in (("PM2.5", 500), ("PM10", 600), ("NO2", 600), ("SO2", 2000)):
            values = [sub_index(pollutant, top * i / 200) for i in range(201)]
            assert values == sorted(values), f"{pollutant} not monotonic"

    def test_never_leaves_scale(self) -> None:
        for pollutant in ("PM2.5", "PM10", "NO2", "CO", "Ozone", "SO2", "NH3"):
            for concentration in (0.0, 0.06, 1.0, 1e9):
                assert 0.0 <= sub_index(pollutant, concentration) <= 500.0

    def test_detection_floor_maps_to_zero(self) -> None:
        """A value below the floor is noise, not a tiny amount of pollution."""
        assert sub_index("PM2.5", 0.01) == 0.0
        assert sub_index("CO", 0.001) == 0.0

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            sub_index("PM2.5", -1.0)

    def test_rejects_unsupported(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            sub_index("Krypton", 1.0)


class TestAliases:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("pm2.5", "PM2.5"),
            ("pm25", "PM2.5"),
            ("PM2.5", "PM2.5"),
            ("pm10", "PM10"),
            ("o3", "Ozone"),
            ("Ozone", "Ozone"),
            ("no2", "NO2"),
        ],
    )
    def test_normalisation(self, raw: str, expected: str) -> None:
        assert canonical_pollutant(raw) == expected

    def test_unknown_returns_none(self) -> None:
        assert canonical_pollutant("Xylene") is None


class TestCompute:
    def test_max_not_mean(self) -> None:
        """CPCB reports the worst sub-index, not an average of pollutants."""
        result = compute({"PM2.5": 60.0, "PM10": 50.0, "NO2": 40.0})
        assert result.aqi == pytest.approx(100.0)
        assert result.dominant_pollutant == "PM2.5"

    def test_dominant_pollutant_identified(self) -> None:
        result = compute({"PM2.5": 10.0, "PM10": 400.0, "NO2": 40.0})
        assert result.dominant_pollutant == "PM10"

    def test_requires_three_pollutants(self) -> None:
        """One or two readings must not become a national AQI."""
        with pytest.raises(InsufficientDataError, match=str(MINIMUM_POLLUTANTS)):
            compute({"PM2.5": 100.0, "PM10": 100.0})

    def test_requires_particulate(self) -> None:
        """CPCB will not publish an AQI from gases alone."""
        with pytest.raises(InsufficientDataError, match=r"PM2\.5 or PM10"):
            compute({"NO2": 90.0, "SO2": 90.0, "CO": 5.0})

    def test_three_with_particulate_is_enough(self) -> None:
        result = compute({"PM2.5": 45.0, "NO2": 30.0, "SO2": 20.0})
        assert result.pollutant_count == 3
        # PM2.5 45 sits halfway through the Satisfactory band: 50 + 25 = 75.
        assert result.aqi == pytest.approx(75.0)
        assert result.category is AQCategory.SATISFACTORY

    def test_diagnostic_mode_allows_one(self) -> None:
        result = compute({"PM2.5": 95.0}, require_minimum=False)
        # 200 at 90 µg/m³ rising to 300 at 120: 200 + (5/30)*100.
        assert result.aqi == pytest.approx(216.666, abs=1e-2)

    def test_ignores_unsupported_and_negative(self) -> None:
        result = compute({"PM2.5": 60.0, "PM10": 100.0, "NO2": 80.0, "Xylene": 999.0, "SO2": -5.0})
        assert result.pollutant_count == 3
        assert all(s.pollutant != "Xylene" for s in result.sub_indices)

    def test_aliases_merge_to_one_pollutant(self) -> None:
        result = compute({"pm2.5": 60.0, "PM10": 100.0, "no2": 80.0})
        names = [s.pollutant for s in result.sub_indices]
        assert names.count("PM2.5") == 1

    def test_empty_raises(self) -> None:
        with pytest.raises(InsufficientDataError):
            compute({})

    def test_category_is_consistent_with_aqi(self) -> None:
        for pm25 in (5.0, 45.0, 75.0, 105.0, 200.0, 400.0):
            result = compute({"PM2.5": pm25, "PM10": 10.0, "NO2": 10.0})
            assert result.category is not None

    def test_try_compute_returns_none_instead_of_raising(self) -> None:
        assert try_compute({"PM2.5": 100.0}) is None
        assert try_compute({"PM2.5": 100.0, "PM10": 100.0, "NO2": 50.0}) is not None


class TestRealisticSamples:
    def test_clean_day(self) -> None:
        result = compute({"PM2.5": 18.0, "PM10": 40.0, "NO2": 22.0, "SO2": 8.0})
        assert result.category is AQCategory.GOOD
        assert result.aqi < 50

    def test_kolkata_winter(self) -> None:
        """Kolkata routinely sits in Moderate-to-Poor on winter nights."""
        result = compute({"PM2.5": 95.0, "PM10": 180.0, "NO2": 70.0, "CO": 1.4})
        assert result.category is AQCategory.POOR
        assert result.dominant_pollutant == "PM2.5"

    def test_units_matter_for_co(self) -> None:
        """CO is mg/m³, not µg/m³. Reading it as µg/m³ would saturate the scale."""
        as_mg = compute({"CO": 2.0, "PM2.5": 10.0, "PM10": 20.0})
        assert as_mg.aqi < 200
