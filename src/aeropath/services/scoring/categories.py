"""CPCB National Air Quality Index bands and sensitivity advisories.

Reference: CPCB National AQI ("One Number, One Colour, One Description").
Six categories over 0-500, built as the maximum sub-index across eight
pollutants. We never compute the index here — we only classify a value that
the provider or a monitoring station already computed, plus decide what a
person with a given sensitivity should do about it.
"""

from __future__ import annotations

import math

from aeropath.schemas.common import AQCategory, Advisory, Sensitivity

AQI_FLOOR = 0
AQI_CEILING = 500

# Exclusive upper bounds, in CPCB order. AQI is reported as an integer, so
# classification rounds first: 50.4 -> Good, 50.5 -> Satisfactory.
_BANDS: tuple[tuple[int, AQCategory], ...] = (
    (50, AQCategory.GOOD),
    (100, AQCategory.SATISFACTORY),
    (200, AQCategory.MODERATE),
    (300, AQCategory.POOR),
    (400, AQCategory.VERY_POOR),
    (500, AQCategory.SEVERE),
)

# Colour must never be the only signal (PRD accessibility requirement), so
# every category carries a short text label for the client to render.
LABELS: dict[AQCategory, str] = {
    AQCategory.GOOD: "Good",
    AQCategory.SATISFACTORY: "Satisfactory",
    AQCategory.MODERATE: "Moderate",
    AQCategory.POOR: "Poor",
    AQCategory.VERY_POOR: "Very Poor",
    AQCategory.SEVERE: "Severe",
}

# Ordered from best to worst for range comparisons.
_SEVERITY_ORDER: tuple[AQCategory, ...] = tuple(cat for _, cat in _BANDS)


def clamp_aqi(value: float) -> float:
    """Clamp to the CPCB reportable range."""
    return max(AQI_FLOOR, min(AQI_CEILING, value))


def round_half_up(value: float) -> int:
    """Round to the nearest integer, with .5 always going up.

    Not Python's built-in ``round``: that is banker's rounding, so 50.5 -> 50
    while 51.5 -> 52. At a health threshold the direction must be consistent
    and must never flatter the air, so a boundary value rounds up.
    """
    return math.floor(value + 0.5)


def classify(value: float) -> AQCategory:
    """Map an AQI value to its CPCB category.

    Rounds to the nearest integer because CPCB publishes integer AQI and the
    band edges are integers; 50.5 must land in Satisfactory, not Good.
    """
    rounded = round_half_up(clamp_aqi(value))
    for upper_bound, category in _BANDS:
        if rounded <= upper_bound:
            return category
    return AQCategory.SEVERE


def label(category: AQCategory) -> str:
    return LABELS[category]


def severity_index(category: AQCategory) -> int:
    """0 for Good, 5 for Severe. Higher means worse air."""
    return _SEVERITY_ORDER.index(category)


def is_at_least(category: AQCategory, threshold: AQCategory) -> bool:
    return severity_index(category) >= severity_index(threshold)


def advisory(category: AQCategory, sensitivity: Sensitivity) -> Advisory:
    """What a person with this sensitivity should do, given the air.

    Kept separate from the category on purpose. The category is a fact about
    the air and is identical for everyone; the advisory is advice about the
    person. Recolouring the map for sensitive users would misreport air
    quality, so guidance rides alongside the colour instead of replacing it.

    Thresholds follow PRD 9.4's guidance column: at Moderate, sensitive users
    should take care; at Poor and above, everyone should avoid outdoor
    exercise on that route.
    """
    if sensitivity is Sensitivity.NORMAL:
        if is_at_least(category, AQCategory.POOR):
            return Advisory.AVOID
        if category is AQCategory.MODERATE:
            return Advisory.CAUTION
        return Advisory.OK

    if sensitivity is Sensitivity.SENSITIVE:
        if is_at_least(category, AQCategory.POOR):
            return Advisory.AVOID
        if category is AQCategory.SATISFACTORY or category is AQCategory.MODERATE:
            return Advisory.CAUTION
        return Advisory.OK

    # High sensitivity: conservative by construction.
    if is_at_least(category, AQCategory.MODERATE):
        return Advisory.AVOID
    if category is AQCategory.SATISFACTORY:
        return Advisory.CAUTION
    return Advisory.OK
