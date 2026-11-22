"""Tests for physical inhaled dose model (Sprint 2 - R1)."""

from __future__ import annotations

import pytest

from aeropath.schemas.common import Activity, RoadClass, Sensitivity
from aeropath.services.scoring.engine import (
    SegmentInput,
    compute_physical_dose,
    score_route,
)


def test_physical_dose_formula_from_pm25_and_duration() -> None:
    # 10 minutes at 50 µg/m³ PM2.5, ventilation 15 L/min (walk)
    # Dose = 50 µg/m³ * (15 / 1000 m³/min) * 10 min = 7.5 µg PM2.5
    dose = compute_physical_dose(
        pm25_ug_m3=50.0,
        duration_min=10.0,
        ventilation_l_min=15.0,
    )
    assert dose == pytest.approx(7.5)


def test_dose_scales_with_duration() -> None:
    dose_10m = compute_physical_dose(pm25_ug_m3=80.0, duration_min=10.0, ventilation_l_min=15.0)
    dose_20m = compute_physical_dose(pm25_ug_m3=80.0, duration_min=20.0, ventilation_l_min=15.0)
    assert dose_20m == pytest.approx(2.0 * dose_10m)


def test_longer_route_through_cleaner_air_has_lower_dose_than_shorter_polluted() -> None:
    from aeropath.services.scoring.street_model import IDENTITY_MODEL

    # Route A (Longer, but Clean): 2000m, PM2.5 = 20 µg/m³
    # Speed = 1.34 m/s -> Duration = 2000 / 1.34 = 1492.5 s = 24.87 min
    # Dose = 20 * 0.015 * 24.87 = 7.46 µg
    seg_clean = SegmentInput(
        segment_id=1,
        length_m=2000.0,
        base_aqi=30.0,
        road_class=RoadClass.RESIDENTIAL,
        pm25_concentration=20.0,
    )
    score_clean = score_route(
        [seg_clean],
        params=IDENTITY_MODEL,
        hour_of_day=12,
        activity=Activity.WALK,
        sensitivity=Sensitivity.NORMAL,
    )

    # Route B (Shorter, but Polluted): 1000m, PM2.5 = 180 µg/m³
    # Duration = 1000 / 1.34 = 746.2 s = 12.44 min
    # Dose = 180 * 0.015 * 12.44 = 33.58 µg
    seg_polluted = SegmentInput(
        segment_id=2,
        length_m=1000.0,
        base_aqi=250.0,
        road_class=RoadClass.PRIMARY,
        pm25_concentration=180.0,
    )
    score_polluted = score_route(
        [seg_polluted],
        params=IDENTITY_MODEL,
        hour_of_day=12,
        activity=Activity.WALK,
        sensitivity=Sensitivity.NORMAL,
    )

    # Clean route is twice as long, but delivers ~4.5x lower inhaled dose!
    assert score_clean.dose < score_polluted.dose
    assert score_clean.dose == pytest.approx(7.46, abs=0.5)
    assert score_polluted.dose == pytest.approx(33.58, abs=1.5)
