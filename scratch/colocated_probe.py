"""Scratch: find co-located stations, which break inverse-distance weighting."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from phase0_validate import collect_station_data, haversine_km  # noqa: E402

CITY = sys.argv[1] if len(sys.argv) > 1 else "Delhi"


async def main() -> None:
    stations, _ = await collect_station_data(CITY, start=date(2024, 11, 1), end=date(2024, 11, 1))
    print(f"{CITY}: {len(stations)} stations")
    pairs = []
    for i, a in enumerate(stations):
        for b in stations[i + 1 :]:
            distance = haversine_km(a.coordinate, b.coordinate)
            if distance < 0.5:
                pairs.append((a.station_id, b.station_id, round(distance, 4), a.name, b.name))
    print(f"pairs closer than 500 m: {len(pairs)}")
    for pair in pairs:
        print(f"  {pair[0]:>10} {pair[1]:>10} {pair[2]:>8.4f} km  {pair[3][:34]} | {pair[4][:34]}")

    # Which stations have no neighbours at all within any usable radius?
    lonely = []
    for a in stations:
        nearest = min(
            (haversine_km(a.coordinate, b.coordinate) for b in stations if b.station_id != a.station_id),
            default=float("inf"),
        )
        if nearest > 20:
            lonely.append((a.station_id, round(nearest, 2), a.name))
    print(f"\nstations with no neighbour within 20 km: {len(lonely)}")
    for item in lonely:
        print(f"  {item[0]:>10} nearest={item[1]:>7.2f} km {item[2][:40]}")


asyncio.run(main())
