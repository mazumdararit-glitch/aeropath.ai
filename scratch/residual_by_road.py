"""Scratch: does road class explain the interpolation residual?

The leave-one-out harness asks whether adjusting a station's traffic-influenced
value improves its prediction. But the *baseline* comes from neighbouring
stations, which also sit on roads, so the interpolation already encodes an
average road effect. Comparing the two directly partially cancels the signal.

This asks the cleaner question: **are stations on busy roads systematically
dirtier than their neighbours predict?** If yes, road_factor has real signal and
is worth calibrating. If no, the factor is noise and the gate is right to fail.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import asyncio

from phase0_validate import (
    AQI_PARAMETERS,
    attach_osm_context,
    collect_station_data,
    haversine_km,
)
from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.schemas.common import RoadClass

OUT = Path(__file__).with_suffix(".log")


async def main() -> None:
    start, end = date(2024, 11, 1), date(2024, 11, 5)
    stations, grouped = await collect_station_data("Kolkata", start=start, end=end)
    contexts = attach_osm_context(stations, cache_dir=REPO_ROOT / "scratch" / "osm_cache")

    by_hour: dict = {}
    for (sid, hour), pollutants in grouped.items():
        by_hour.setdefault(hour, {})[sid] = pollutants

    truth: dict[tuple[str, object], float] = {}
    for hour, readings in by_hour.items():
        for sid, pollutants in readings.items():
            try:
                truth[(sid, hour)] = cpcb_aqi.compute(pollutants).aqi
            except InsufficientDataError:
                continue

    coords = {s.station_id: s.coordinate for s in stations}
    hours = sorted({h for _, h in truth})

    residuals: dict[str, list[float]] = defaultdict(list)
    for hour in hours:
        present = {s: v for (s, h), v in truth.items() if h == hour}
        for sid, actual in present.items():
            others = {s: v for s, v in present.items() if s != sid and s in coords}
            if not others or actual <= 0:
                continue
            weights = {s: 1.0 / haversine_km(coords[sid], coords[s]) ** 2 for s in others}
            predicted = sum(others[s] * w for s, w in weights.items()) / sum(weights.values())
            residuals[sid].append((actual - predicted) / actual)

    lines = [f"{'station':>10} {'road':>13} {'n':>5} {'mean residual':>14} {'median':>9}"]
    by_class: dict[str, list[float]] = defaultdict(list)
    for sid, values in residuals.items():
        if not values:
            continue
        road = contexts[sid].road_class.value if sid in contexts else "unknown"
        mean = sum(values) / len(values)
        ordered = sorted(values)
        median = ordered[len(ordered) // 2]
        lines.append(f"{sid:>10} {road:>13} {len(values):>5} {mean:>+14.2%} {median:>+9.2%}")
        by_class[road].extend(values)

    lines.append("")
    lines.append(f"{'road class':>13} {'n':>5} {'mean residual':>14}")
    for road, values in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"{road:>13} {len(values):>5} {sum(values) / len(values):>+14.2%}")

    lines.append("")
    lines.append("A positive mean residual means the station is dirtier than its")
    lines.append("neighbours predict. If busy-road classes are systematically positive,")
    lines.append("road_factor carries real signal and is worth calibrating.")

    text = "\n".join(lines)
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
