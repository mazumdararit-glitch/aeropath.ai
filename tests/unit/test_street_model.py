"""The street-level adjustment model."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aeropath.schemas.common import RoadClass
from aeropath.services.scoring import street_model
from aeropath.services.scoring.street_model import (
    DEFAULT_MODEL,
    MAX_RELATIVE_ADJUSTMENT,
    StreetModelParams,
    adjust_aqi,
    adjust_concentrations,
    green_factor,
    local_hour,
    proximity_factor,
    road_factor,
    time_factor,
    water_factor,
)


class TestRoadFactors:
    def test_busy_roads_are_dirtier_than_quiet_paths(self) -> None:
        motorway = road_factor(DEFAULT_MODEL, RoadClass.MOTORWAY)
        residential = road_factor(DEFAULT_MODEL, RoadClass.RESIDENTIAL)
        path = road_factor(DEFAULT_MODEL, RoadClass.PATH)
        assert motorway > residential > path

    def test_unknown_road_class_is_neutral(self) -> None:
        """Unmapped OSM values must not silently inflate exposure."""
        assert road_factor(DEFAULT_MODEL, RoadClass.UNKNOWN) == pytest.approx(1.0)

    @pytest.mark.parametrize("road_class", list(RoadClass))
    def test_every_road_class_has_a_factor(self, road_class: RoadClass) -> None:
        assert road_class in DEFAULT_MODEL.road_factors


class TestProximityDecay:
    def test_full_effect_at_zero_distance(self) -> None:
        assert proximity_factor(0, 0.15, 150) == pytest.approx(0.85)

    def test_decays_toward_one(self) -> None:
        near = proximity_factor(0, 0.15, 150)
        mid = proximity_factor(150, 0.15, 150)
        far = proximity_factor(10_000, 0.15, 150)
        assert near < mid < far
        # exp(-66) underflows, so "far" is exactly 1.0 rather than merely close.
        assert far <= 1.0

    def test_approaches_unity(self) -> None:
        assert proximity_factor(1e9, 0.15, 150) == pytest.approx(1.0)

    def test_monotonic(self) -> None:
        values = [proximity_factor(d, 0.15, 150) for d in range(0, 1000, 25)]
        assert values == sorted(values)

    def test_rejects_negative_distance(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            proximity_factor(-1, 0.15, 150)

    def test_green_is_gentler_than_water(self) -> None:
        """A park should lower the estimate more than a lake at equal distance."""
        assert green_factor(DEFAULT_MODEL, 80) < water_factor(DEFAULT_MODEL, 80)


class TestTimeFactor:
    def test_rush_hour_is_worse(self) -> None:
        assert time_factor(DEFAULT_MODEL, 9) > time_factor(DEFAULT_MODEL, 14)

    def test_night_is_cleaner(self) -> None:
        assert time_factor(DEFAULT_MODEL, 3) < time_factor(DEFAULT_MODEL, 14)

    def test_neutral_at_midday(self) -> None:
        assert time_factor(DEFAULT_MODEL, 14) == pytest.approx(1.0)

    @pytest.mark.parametrize("hour", [-1, 24, 99])
    def test_rejects_invalid_hours(self, hour: int) -> None:
        with pytest.raises(ValueError, match="0-23"):
            time_factor(DEFAULT_MODEL, hour)

    def test_rush_and_night_do_not_overlap(self) -> None:
        """An hour in both sets would make the result depend on set ordering."""
        assert not (DEFAULT_MODEL.rush_hours & DEFAULT_MODEL.night_hours)


class TestAdjustment:
    def test_respects_the_cap(self) -> None:
        """No single cell may be adjusted more than the cap in either direction.

        This is the guard against a rules model claiming resolution the 500 m
        grid does not have, and landing users in the wrong colour.
        """
        malicious = StreetModelParams(
            version="test",
            road_factors=dict.fromkeys(RoadClass, 5.0),
            rush_factor=5.0,
            night_factor=0.01,
            green_attenuation=0.9,
        )
        low, _ = adjust_aqi(100.0, malicious, road_class=RoadClass.MOTORWAY, hour_of_day=14)
        assert low <= 100.0 * (1 + MAX_RELATIVE_ADJUSTMENT) + 1e-9

        dirty, _ = adjust_aqi(
            100.0,
            malicious,
            road_class=RoadClass.MOTORWAY,
            hour_of_day=9,
            green_distance_m=0.0,
            water_distance_m=0.0,
        )
        assert dirty <= 100.0 * (1 + MAX_RELATIVE_ADJUSTMENT) + 1e-9

    def test_clean_path_is_lowered(self) -> None:
        value, factors = adjust_aqi(
            100.0,
            DEFAULT_MODEL,
            road_class=RoadClass.PATH,
            hour_of_day=3,
            green_distance_m=10.0,
        )
        assert value < 100.0
        assert factors["road"] < 1.0
        assert factors["time"] < 1.0
        assert factors["green"] < 1.0

    def test_busy_road_at_rush_hour_is_raised(self) -> None:
        value, _ = adjust_aqi(100.0, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9)
        assert value > 100.0

    def test_returns_every_factor_for_explanation(self) -> None:
        """The planner may only cite numbers the engine emitted."""
        _, factors = adjust_aqi(
            80.0,
            DEFAULT_MODEL,
            road_class=RoadClass.PRIMARY,
            hour_of_day=9,
            green_distance_m=40.0,
            water_distance_m=200.0,
            weather_factor=1.05,
        )
        assert set(factors) == {"road", "time", "green", "water", "weather"}

    def test_omits_absent_factor_inputs(self) -> None:
        _, factors = adjust_aqi(80.0, DEFAULT_MODEL, road_class=RoadClass.PRIMARY, hour_of_day=14)
        assert "green" not in factors
        assert "water" not in factors

    def test_neutral_model_is_identity(self) -> None:
        neutral = StreetModelParams(version="test", road_factors=dict.fromkeys(RoadClass, 1.0))
        value, _ = adjust_aqi(123.0, neutral, road_class=RoadClass.TERTIARY, hour_of_day=14)
        assert value == pytest.approx(123.0)

    def test_zero_base_stays_zero(self) -> None:
        value, _ = adjust_aqi(0.0, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9)
        assert value == pytest.approx(0.0)

    def test_rejects_negative_base(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            adjust_aqi(-5.0, DEFAULT_MODEL, road_class=RoadClass.TERTIARY, hour_of_day=14)

    def test_rejects_non_positive_weather(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            adjust_aqi(
                50.0,
                DEFAULT_MODEL,
                road_class=RoadClass.TERTIARY,
                hour_of_day=14,
                weather_factor=0.0,
            )

    def test_is_deterministic(self) -> None:
        first, _ = adjust_aqi(88.8, DEFAULT_MODEL, road_class=RoadClass.SECONDARY, hour_of_day=18)
        second, _ = adjust_aqi(88.8, DEFAULT_MODEL, road_class=RoadClass.SECONDARY, hour_of_day=18)
        assert first == second


class TestConcentrationAdjustment:
    """The model scales concentrations; the index is recomputed afterwards.

    Multuplying a finished AQI is the PRD's formulation and it is wrong: the CPCB
    sub-index is piecewise-linear with uneven bands, so ``aqi x 1.35`` and
    ``aqi(concentration x 1.35)`` disagree by tens of AQI points and in opposite
    directions in different bands.
    """

    COMPOSITION = {"PM2.5": 95.0, "PM10": 180.0, "NO2": 70.0, "CO": 1.4}

    def test_scales_every_pollutant(self) -> None:
        scaled, _ = adjust_concentrations(
            self.COMPOSITION, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9
        )
        for name, value in self.COMPOSITION.items():
            assert scaled[name] > value

    def test_matches_the_multiplier(self) -> None:
        scaled, factors = adjust_concentrations(
            self.COMPOSITION, DEFAULT_MODEL, road_class=RoadClass.TERTIARY, hour_of_day=14
        )
        expected = factors["road"] * factors["time"] * factors["weather"]
        assert scaled["PM2.5"] == pytest.approx(self.COMPOSITION["PM2.5"] * expected)

    def test_differs_from_scaling_the_index(self) -> None:
        """The reason the two paths are not interchangeable."""
        from aeropath.domain import cpcb_aqi

        raw = cpcb_aqi.compute(self.COMPOSITION).aqi
        aqi_path, _ = adjust_aqi(raw, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9)
        scaled, _ = adjust_concentrations(
            self.COMPOSITION, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9
        )
        conc_path = cpcb_aqi.compute(scaled).aqi
        assert aqi_path != pytest.approx(conc_path, abs=0.5)

    def test_result_stays_within_the_published_scale(self) -> None:
        """Adjusting concentrations cannot leave 0-500; nothing is clamped away."""
        from aeropath.domain import cpcb_aqi

        for pm25 in (5.0, 45.0, 95.0, 200.0, 480.0):
            scaled, _ = adjust_concentrations(
                {"PM2.5": pm25, "PM10": pm25 * 2, "NO2": 40.0},
                DEFAULT_MODEL,
                road_class=RoadClass.MOTORWAY,
                hour_of_day=9,
            )
            assert 0.0 <= cpcb_aqi.compute(scaled).aqi <= 500.0

    def test_never_produces_negative_concentrations(self) -> None:
        scaled, _ = adjust_concentrations(
            {"PM2.5": 0.06, "PM10": 0.06, "NO2": 0.06},
            DEFAULT_MODEL,
            road_class=RoadClass.PATH,
            hour_of_day=3,
        )
        assert all(v >= 0 for v in scaled.values())


class TestUniformMultiplierCannotReorder:
    def test_uniform_multiplier_preserves_order(self) -> None:
        """A model with only time or weather factors can never change a ranking.

        This is why Phase 0 must supply per-segment road context: without it the
        multiplier is identical for every station and the model is a no-op that
        looks like it ran.
        """
        values = [40.0, 90.0, 210.0]
        neutral = StreetModelParams(version="test", road_factors=dict.fromkeys(RoadClass, 1.0))
        scaled = [
            adjust_aqi(v, neutral, road_class=RoadClass.TERTIARY, hour_of_day=9)[0] for v in values
        ]
        assert scaled == sorted(scaled)
        assert all(a < b for a, b in zip(values, scaled, strict=True))


class TestLocalHour:
    def test_kolkata_offset(self) -> None:
        """A 5:30 offset moves the rush-hour window; naive times must not pass."""
        ist = timezone(timedelta(hours=5, minutes=30))
        moment = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
        assert local_hour(moment) == 9
        assert local_hour(moment.astimezone(ist)) == 15
        assert local_hour(datetime(2026, 11, 20, 9, 0, tzinfo=ist)) == 9

    def test_rejects_naive_datetimes(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            local_hour(datetime(2026, 11, 20, 9, 0))

    def test_handles_midnight(self) -> None:
        ist = timezone(timedelta(hours=5, minutes=30))
        assert local_hour(datetime(2026, 11, 20, 0, 5, tzinfo=ist)) == 0


def test_no_nan_survives_adjustment() -> None:
    value, factors = adjust_aqi(
        100.0,
        DEFAULT_MODEL,
        road_class=RoadClass.CYCLEWAY,
        hour_of_day=7,
        green_distance_m=1e-9,
        water_distance_m=1e-9,
    )
    assert not math.isnan(value)
    assert all(not math.isnan(f) for f in factors.values())


def test_module_exposes_model_constant() -> None:
    assert street_model.DEFAULT_MODEL.version == "rules_v1"
