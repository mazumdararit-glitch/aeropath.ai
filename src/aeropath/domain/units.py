"""Unit normalisation for pollutant concentrations.

Every CPCB anchor in ``cpcb_aqi`` is defined in a specific unit, and two of them
are not the µg/m³ that most sources emit:

* PM2.5, PM10, NO2, SO2, NH3, Ozone → **µg/m³**
* CO → **mg/m³**

The CO mismatch is not cosmetic. The CPCB CO scale runs 0 → 1.0 → 2.0 mg/m³ for
the first 100 index points, so a source reporting 3675 µg/m³ of CO (a normal
Indian city value, and exactly what Open-Meteo returns) falls far above the
final 50 mg/m³ anchor and saturates the sub-index at **500 — Severe**. A unit
error alone would turn clean morning air into a health emergency.

So units are converted by explicit table and anything unrecognised raises.
Guessing would reintroduce precisely that failure.
"""

from __future__ import annotations

# The unit each CPCB anchor set is expressed in.
CANONICAL_UNITS: dict[str, str] = {
    "PM2.5": "µg/m³",
    "PM10": "µg/m³",
    "NO2": "µg/m³",
    "SO2": "µg/m³",
    "NH3": "µg/m³",
    "Ozone": "µg/m³",
    "CO": "mg/m³",
}

# How a source's unit string maps to a multiplier toward the canonical unit.
# Keys are normalised (lowercased, whitespace stripped, ug/ml variants unified).
_TO_CANONICAL: dict[str, dict[str, float]] = {
    "µg/m³": {
        "ug/m3": 1.0,
        "µg/m3": 1.0,
        "ug/m³": 1.0,
        "µg/m³": 1.0,
        "micrograms per cubic meter": 1.0,
        "MICROGRAMS_PER_CUBIC_METER": 1.0,
    },
    "mg/m³": {
        "mg/m3": 1.0,
        "mg/m³": 1.0,
        "milligrams per cubic meter": 1.0,
        "MILLIGRAMS_PER_CUBIC_METER": 1.0,
        "ug/m3": 0.001,
        "µg/m3": 0.001,
        "ug/m³": 0.001,
        "µg/m³": 0.001,
        "micrograms per cubic meter": 0.001,
        "MICROGRAMS_PER_CUBIC_METER": 0.001,
    },
}


class UnknownUnitError(ValueError):
    """A concentration arrived in a unit we refuse to guess about."""


def _normalise_label(unit: str) -> str:
    return " ".join(unit.strip().split())


def conversion_factor(pollutant: str, unit: str) -> float:
    """Multiplier that converts ``unit`` to the pollutant's canonical unit."""
    canonical = CANONICAL_UNITS.get(pollutant)
    if canonical is None:
        raise UnknownUnitError(f"unsupported pollutant: {pollutant}")

    table = _TO_CANONICAL[canonical]
    label = _normalise_label(unit)
    if label in table:
        return table[label]
    # Case-insensitive fallback, but only where it is unambiguous.
    lowered = label.lower()
    for key, factor in table.items():
        if key.lower() == lowered:
            return factor
    raise UnknownUnitError(
        f"unknown unit {unit!r} for {pollutant}; expected one of {sorted(set(table))}"
    )


def to_canonical(pollutant: str, value: float, unit: str) -> float:
    """Convert one concentration to the unit the CPCB anchors expect."""
    return value * conversion_factor(pollutant, unit)


def normalise(concentrations: dict[str, float], units: dict[str, str]) -> dict[str, float]:
    """Convert a batch, skipping pollutants whose unit is absent or unknown.

    Skipping rather than raising at the batch level keeps one odd pollutant from
    discarding a usable reading; the CPCB minimum-pollutant rule then decides
    whether what remains is reportable.
    """
    out: dict[str, float] = {}
    for pollutant, value in concentrations.items():
        unit = units.get(pollutant)
        if unit is None:
            continue
        try:
            out[pollutant] = to_canonical(pollutant, value, unit)
        except UnknownUnitError:
            continue
    return out
