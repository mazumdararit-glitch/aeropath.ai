"""Tests for Task R3: Street model v2 (rules_v2, wind, canyon, vegetation & water)."""

from __future__ import annotations

import math

import pytest

from aeropath.services.scoring.street_model import (
    RULES_V2_MODEL,
    canyon_wind_factor,
    proximity_v2_factor,
)


def test_proximity_v2_exponential_decay() -> None:
    """Proximity attenuation decreases exponentially with distance."""
    # At 0m distance: 1.0 - 0.18 = 0.82
    f_0 = proximity_v2_factor(0.0, reduction=0.18, decay_m=50.0)
    assert f_0 == pytest.approx(0.82)

    # At 50m distance (1 decay length): 1.0 - 0.18 * exp(-1) = ~0.9338
    f_50 = proximity_v2_factor(50.0, reduction=0.18, decay_m=50.0)
    assert f_50 == pytest.approx(1.0 - 0.18 * math.exp(-1))

    # At large distance: asymptotically approaches 1.0
    f_large = proximity_v2_factor(500.0, reduction=0.18, decay_m=50.0)
    assert f_large == pytest.approx(1.0, abs=1e-3)


def test_canyon_wind_dispersion_and_vortex() -> None:
    """Wind perpendicular to street in canyon increases leeward concentration; high wind dilutes."""
    params = RULES_V2_MODEL

    # Calm wind (0.5 m/s) with perpendicular wind (90 deg crosswind): vortex trap
    f_vortex = canyon_wind_factor(
        wind_speed_mps=0.5,
        wind_dir_deg=90.0,
        street_bearing_deg=0.0,
        params=params,
    )
    assert f_vortex > 1.0, "Crosswind in street canyon should trap pollutants on leeward side"

    # High wind (8.0 m/s) along street (parallel): strong ventilation dilution
    f_diluted = canyon_wind_factor(
        wind_speed_mps=8.0,
        wind_dir_deg=0.0,
        street_bearing_deg=0.0,
        params=params,
    )
    assert f_diluted < 1.0, "High parallel wind should dilute concentration"
    assert f_diluted < f_vortex


def test_rules_v2_model_configuration() -> None:
    """RULES_V2_MODEL contains the version tag and physical parameters."""
    assert RULES_V2_MODEL.version == "rules_v2"
    assert RULES_V2_MODEL.canyon_leeward_factor > 1.0
    assert RULES_V2_MODEL.vegetation_reduction_factor > 0.0
    assert RULES_V2_MODEL.vegetation_decay_length_m == 50.0
