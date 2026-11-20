"""CPCB band classification and sensitivity advisories."""

from __future__ import annotations

import pytest

from aeropath.schemas.common import Advisory, AQCategory, Sensitivity
from aeropath.services.scoring import categories


class TestClassification:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, AQCategory.GOOD),
            (25, AQCategory.GOOD),
            (50, AQCategory.GOOD),
            (51, AQCategory.SATISFACTORY),
            (100, AQCategory.SATISFACTORY),
            (101, AQCategory.MODERATE),
            (200, AQCategory.MODERATE),
            (201, AQCategory.POOR),
            (300, AQCategory.POOR),
            (301, AQCategory.VERY_POOR),
            (400, AQCategory.VERY_POOR),
            (401, AQCategory.SEVERE),
            (500, AQCategory.SEVERE),
        ],
    )
    def test_band_edges(self, value: float, expected: AQCategory) -> None:
        """Every CPCB boundary, on both sides. These are the colours users see."""
        assert categories.classify(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (50.4, AQCategory.GOOD),
            (50.5, AQCategory.SATISFACTORY),
            (100.49, AQCategory.SATISFACTORY),
            (100.5, AQCategory.MODERATE),
        ],
    )
    def test_rounding_at_edges(self, value: float, expected: AQCategory) -> None:
        assert categories.classify(value) is expected

    def test_clamps_out_of_range(self) -> None:
        assert categories.classify(-40) is AQCategory.GOOD
        assert categories.classify(9000) is AQCategory.SEVERE

    def test_severity_is_ordered(self) -> None:
        ordered = [
            AQCategory.GOOD,
            AQCategory.SATISFACTORY,
            AQCategory.MODERATE,
            AQCategory.POOR,
            AQCategory.VERY_POOR,
            AQCategory.SEVERE,
        ]
        indices = [categories.severity_index(c) for c in ordered]
        assert indices == sorted(indices)

    @pytest.mark.parametrize("category", list(AQCategory))
    def test_every_category_has_a_text_label(self, category: AQCategory) -> None:
        """Colour must never be the only signal (PRD accessibility)."""
        assert categories.label(category)


class TestAdvisory:
    @pytest.mark.parametrize(
        ("category", "sensitivity", "expected"),
        [
            (AQCategory.GOOD, Sensitivity.NORMAL, Advisory.OK),
            (AQCategory.GOOD, Sensitivity.HIGH, Advisory.OK),
            (AQCategory.SATISFACTORY, Sensitivity.NORMAL, Advisory.OK),
            (AQCategory.SATISFACTORY, Sensitivity.SENSITIVE, Advisory.CAUTION),
            (AQCategory.SATISFACTORY, Sensitivity.HIGH, Advisory.CAUTION),
            (AQCategory.MODERATE, Sensitivity.NORMAL, Advisory.CAUTION),
            (AQCategory.MODERATE, Sensitivity.SENSITIVE, Advisory.CAUTION),
            (AQCategory.MODERATE, Sensitivity.HIGH, Advisory.AVOID),
            (AQCategory.POOR, Sensitivity.NORMAL, Advisory.AVOID),
            (AQCategory.POOR, Sensitivity.SENSITIVE, Advisory.AVOID),
            (AQCategory.SEVERE, Sensitivity.NORMAL, Advisory.AVOID),
        ],
    )
    def test_advisory_matrix(
        self, category: AQCategory, sensitivity: Sensitivity, expected: Advisory
    ) -> None:
        assert categories.advisory(category, sensitivity) is expected

    def test_high_sensitivity_is_never_more_permissive(self) -> None:
        """A stricter user must never get looser advice than a normal one."""
        order = [Advisory.OK, Advisory.CAUTION, Advisory.AVOID]
        for category in AQCategory:
            normal = order.index(categories.advisory(category, Sensitivity.NORMAL))
            high = order.index(categories.advisory(category, Sensitivity.HIGH))
            assert high >= normal, f"high sensitivity less strict than normal at {category}"
