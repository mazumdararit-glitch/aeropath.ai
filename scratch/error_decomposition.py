"""Scratch: how much of the error is spatial versus temporal?

The Phase 0 harness predicts a station from its neighbours 2-6 km away and gets
~20% MAPE. This asks whether that error is irreducible (Kolkata's air really
does vary over kilometres) or an artefact of the interpolation.

Two questions:
 1. How well does a station correlate with its *own* recent past? (persistence)
 2. How well does it correlate with *neighbouring stations at the same hour*?

If persistence is much better than the neighbours, the grid resolution is the
problem and more stations would help. If they are comparable, spatial
interpolation is near its ceiling and the street model has to carry the load.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import asyncio

from phase0_validate import AQI_PARAMETERS, collect_station_data, haversine_km
from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.domain.metrics import Pair, category_agreement, error_stats

OUT = Path(__file__).with_suffix(".log")


async def main() -> None:
    start, end = date(2024, 11, 1), date(2024, 11, 5)
    stations, grouped = await collect_station_data("Kolkata", start=start, end=end)

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

    hours = sorted({h for _, h in truth})
    persist: list[Pair] = []
    for sid in {s for s, _ in truth}:
        for hour in hours:
            current = truth.get((sid, hour))
            earlier = [truth.get((sid, h)) for h in hours if h < hour]
            earlier = [e for e in earlier if e is not None]
            if current is not None and earlier:
                persist.append(Pair(predicted=sum(earlier) / len(earlier), actual=current))

    coords = {s.station_id: s.coordinate for s in stations}
    neighbour: list[Pair] = []
    for hour in hours:
        present = {s: v for (s, h), v in truth.items() if h == hour}
        for sid, actual in present.items():
            others = {s: v for s, v in present.items() if s != sid and s in coords}
            if not others:
                continue
            weights = {s: 1.0 / haversine_km(coords[sid], coords[s]) ** 2 for s in others}
            predicted = sum(others[s] * w for s, w in weights.items()) / sum(weights.values())
            neighbour.append(Pair(predicted=predicted, actual=actual))

    lines = []
    for name, pairs in (("persistence", persist), ("neighbour IDW", neighbour)):
        stats = error_stats(name, pairs)
        agree = category_agreement([(p.predicted, p.actual) for p in pairs])
        lines.append(
            f"{name:14s} n={stats.n:5d} mape={stats.mape:6.2f}% "
            f"agreement={agree.rate:6.1%} rmse={stats.rmse:7.2f}"
        )

    lines.append("")
    lines.append("Interpretation: if persistence is far better, the limiter is")
    lines.append("spatial density. If comparable, interpolation is at its ceiling.")
    text = "\n".join(lines)
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
