"""Unit normalisation, with emphasis on the CO trap.

The CPCB CO anchors are in mg/m³ while most sources emit µg/m³. A 3675 µg/m³
reading is a normal Indian city value, but fed into a scale that tops out at
50 mg/m³ it saturates at sub-index 500 — "Severe" — purely from the unit. These
tests exist so that failure cannot be reintroduced silently.
"""

from __future__ import annotations

import pytest

from aeropath.domain import cpcb_aqi
from aeropath.domain.units import (
    CANONICAL_UNITS,
    UnknownUnitError,
    conversion_factor,
    normalise,
    to_canonical,
)


class TestCanonicalUnits:
    def test_all_cpcb_pollutants_have_a_canonical_unit(self) -> None:
        for pollutant in ("PM2.5", "PM10", "NO2", "SO2", "NH3", "Ozone", "CO"):
            assert pollutant in CANONICAL_UNITS

    def test_co_is_milligrams_not_micrograms(self) -> None:
        """The whole trap in one assertion."""
        assert CANONICAL_UNITS["CO"] == "mg/m³"
        assert CANONICAL_UNITS["PM2.5"] == "µg/m³"


class TestConversion:
    def test_micrograms_to_canonical_is_identity(self) -> None:
        assert conversion_factor("PM2.5", "µg/m³") == pytest.approx(1.0)

    def test_units_are_case_and_space_tolerant(self) -> None:
        assert conversion_factor("PM2.5", "  µg/m³  ") == pytest.approx(1.0)
        assert conversion_factor("PM2.5", "ug/m3") == pytest.approx(1.0)

    def test_co_micrograms_converts_to_milligrams(self) -> None:
        assert conversion_factor("CO", "µg/m³") == pytest.approx(0.001)

    def test_co_3675_micrograms_becomes_3_675_milligrams(self) -> None:
        """The value Open-Meteo actually returns for Delhi."""
        assert to_canonical("CO", 3675.0, "µg/m³") == pytest.approx(3.675)

    def test_unknown_unit_raises_rather_than_guessing(self) -> None:
        with pytest.raises(UnknownUnitError, match="unknown unit"):
            conversion_factor("PM2.5", "furlongs")

    def test_unsupported_pollutant_raises(self) -> None:
        with pytest.raises(UnknownUnitError, match="unsupported pollutant"):
            conversion_factor("Radon", "µg/m³")


class TestCoWouldSaturateWithoutConversion:
    def test_raw_micrograms_produce_a_severe_index(self) -> None:
        """This is the bug the conversion prevents, demonstrated."""
        raw = cpcb_aqi.sub_index("CO", 3675.0)
        assert raw == pytest.approx(500.0)

    def test_converted_value_produces_a_plausible_index(self) -> None:
        converted = to_canonical("CO", 3675.0, "µg/m³")
        # 3.675 mg/m³ sits between the 2.0 (AQI 100) and 10.0 (AQI 200) anchors.
        assert 100.0 < cpcb_aqi.sub_index("CO", converted) < 200.0


class TestNormaliseBatch:
    def test_converts_every_known_unit(self) -> None:
        out = normalise(
            {"PM2.5": 60.0, "CO": 4000.0},
            {"PM2.5": "µg/m³", "CO": "µg/m³"},
        )
        assert out["PM2.5"] == pytest.approx(60.0)
        assert out["CO"] == pytest.approx(4.0)

    def test_skips_pollutants_without_a_unit(self) -> None:
        out = normalise({"PM2.5": 60.0, "PM10": 100.0}, {"PM2.5": "µg/m³"})
        assert "PM10" not in out

    def test_skips_unknown_units_without_discarding_the_batch(self) -> None:
        out = normalise(
            {"PM2.5": 60.0, "NO2": 40.0},
            {"PM2.5": "µg/m³", "NO2": "bananas"},
        )
        assert out == {"PM2.5": 60.0}
