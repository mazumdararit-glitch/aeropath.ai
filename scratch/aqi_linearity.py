"""Scratch: does multiplying an AQI by a factor even make sense?

AQI is ``max over pollutants of sub_index(concentration)``, and ``sub_index``
is a *piecewise-linear* map. So ``aqi x f`` is not the same as
``sub_index(concentration x f)``. If that matters, the street model must adjust
concentrations and recompute, not adjust the finished AQI.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aeropath.domain import cpcb_aqi

cases = [10.0, 30.0, 45.0, 60.0, 90.0, 120.0, 200.0]

print(f"{'pm25':>6} {'aqi':>7} {'aqi*1.35':>9} {'aqi(conc*1.35)':>16} {'gap':>8}")
for pm25 in cases:
    base = cpcb_aqi.sub_index("PM2.5", pm25)
    scaled_aqi = base * 1.35
    true = cpcb_aqi.sub_index("PM2.5", pm25 * 1.35)
    print(f"{pm25:>6} {base:>7.1f} {scaled_aqi:>9.1f} {true:>16.1f} {scaled_aqi - true:>+8.1f}")

print()
print("Same question for the full index, where the *maximum* can move to a")
print("different pollutant when one is scaled and the others are not:")
comps = {"PM2.5": 95.0, "PM10": 180.0, "NO2": 70.0}
print("  raw", comps, "->", round(cpcb_aqi.compute(comps).aqi, 1))

scaled_in_aqi = cpcb_aqi.compute(comps).aqi * 1.35
scaled_in_conc = cpcb_aqi.compute({k: v * 1.35 for k, v in comps.items()}).aqi
print(f"  adjust the AQI      -> {scaled_in_aqi:.1f}")
print(f"  adjust each of them -> {scaled_in_conc:.1f}")
print(f"  gap                 -> {scaled_in_aqi - scaled_in_conc:+.1f}")

print()
print("And what a road multiplier means when the dominant pollutant changes:")
road = {"PM2.5": 95.0, "PM10": 180.0, "NO2": 70.0, "CO": 1.4}
print("  dominant pollutant at baseline:", cpcb_aqi.compute(road).dominant_pollutant)
print("  dominant pollutant scaled 1.35:", cpcb_aqi.compute({k: v * 1.35 for k, v in road.items()}).dominant_pollutant)
