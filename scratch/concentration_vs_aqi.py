"""Scratch: adjust concentrations and recompute, versus adjust the AQI.

The Phase 0 run failed with the AQI-multiplier model. This checks whether the
concentration-multiplier model behaves differently, and whether it helps at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aeropath.domain import cpcb_aqi
from aeropath.services.scoring import street_model
from aeropath.services.scoring.street_model import DEFAULT_MODEL

comps = {"PM2.5": 95.0, "PM10": 180.0, "NO2": 70.0, "CO": 1.4}
raw = cpcb_aqi.compute(comps).aqi

aqi_way, _ = street_model.adjust_aqi(
    100.0, DEFAULT_MODEL, road_class=street_model.__dict__["RoadClass"].MOTORWAY, hour_of_day=9
)

from aeropath.schemas.common import RoadClass

aqi_way, f1 = street_model.adjust_aqi(raw, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9)
scaled, f2 = street_model.adjust_concentrations(
    comps, DEFAULT_MODEL, road_class=RoadClass.MOTORWAY, hour_of_day=9
)
conc_way = cpcb_aqi.compute(scaled).aqi

print(f"raw AQI                        {raw:8.2f}")
print(f"multiplier                     {f1['road'] * f1['time']:8.3f}")
print(f"adjust the finished AQI        {aqi_way:8.2f}")
print(f"adjust concentrations, recompute {conc_way:8.2f}")
print(f"difference                     {aqi_way - conc_way:+8.2f}")

print()
print("Does the dominant pollutant change?")
print("  baseline:", cpcb_aqi.compute(comps).dominant_pollutant)
print("  adjusted:", cpcb_aqi.compute(scaled).dominant_pollutant)

print()
print("Is a uniform multiplier ever able to help a *ranking*?")
print("  A uniform scale preserves order: if a > b then a*f > b*f for f > 0.")
print("  So a model with only time/weather factors cannot reorder anything.")
print("  Only road/green/water, which vary per segment, can.")
