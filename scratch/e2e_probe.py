"""End-to-end probe: real OSRM routing plus real CPCB stations.

Not part of the test suite. This exists to prove the wire contracts against live
services, because mocked tests can only prove we agree with our own assumptions.

    python scratch/e2e_probe.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aeropath.domain import cpcb_aqi  # noqa: E402
from aeropath.external.cpcb_stations import DEMO_API_KEY  # noqa: E402
from aeropath.external.osrm import OsrmRouter  # noqa: E402
from aeropath.external.xkdr_provider import XkdrStationProvider  # noqa: E402
from aeropath.schemas.common import Activity, Coordinate, Sensitivity  # noqa: E402
from aeropath.services import routing  # noqa: E402
from aeropath.services.planning import PlanRequest, PlanService  # noqa: E402
from aeropath.services.scoring import engine  # noqa: E402

# Connaught Place to India Gate: a real ~3 km Delhi walk, dense station coverage.
ORIGIN = Coordinate(lat=28.6315, lng=77.2167)
DESTINATION = Coordinate(lat=28.6129, lng=77.2295)

# The public demo key only reads 2024 bulk files, so every data call is pinned
# inside 2024. With a real key this becomes datetime.now(UTC).
DEMO_MOMENT = datetime(2024, 11, 1, 8, tzinfo=UTC)


async def main() -> int:
    print("=== 1. OSRM routing (no key) ===")
    async with OsrmRouter() as router:
        geometries = await router.route(ORIGIN, DESTINATION, profile="foot", alternatives=1)
    print(f"  {len(geometries)} route(s)")
    for i, geometry in enumerate(geometries, 1):
        print(
            f"  route {i}: {geometry.distance_m:.0f} m, "
            f"{geometry.duration_s:.0f} s, {len(geometry.coordinates)} vertices"
        )

    print("\n=== 2. Segmentisation ===")
    segments = routing.segments_from_geometry(geometries[0], spacing_m=100.0)
    total = sum(s.length_m for s in segments)
    print(f"  {len(segments)} segments covering {total:.0f} m")
    print(f"  distinct H3 cells: {len({s.cell for s in segments})}")

    print("\n=== 3. Station provider (demo key, 2024 data) ===")
    points = [seg.coordinate for seg in segments]
    async with XkdrStationProvider(DEMO_API_KEY, city="Delhi") as provider:
        readings = await provider.concentrations_at(points, at=DEMO_MOMENT)

    usable = [r for r in readings if r.concentrations]
    print(f"  {len(usable)}/{len(readings)} points have concentrations")
    if usable:
        sample = usable[0]
        print(f"  nearest station: {sample.nearest_station_km:.1f} km")
        print(f"  confidence: {sample.confidence.value}")
        print(f"  pollutants: {sorted(sample.concentrations)}")
        for pollutant, value in sorted(sample.concentrations.items()):
            print(f"    {pollutant:8} {value:10.3f}")

    print("\n=== 4. CPCB index from those concentrations ===")
    if usable:
        try:
            result = cpcb_aqi.compute(usable[0].concentrations)
            print(f"  AQI {result.aqi:.1f}  {result.category.value}")
            print(f"  dominant: {result.dominant_pollutant}")
            for sub in result.sub_indices:
                print(
                    f"    {sub.pollutant:8} conc={sub.concentration:9.3f} "
                    f"sub-index={sub.sub_index:6.1f} {sub.category.value}"
                )
        except cpcb_aqi.InsufficientDataError as exc:
            print(f"  cannot compute AQI: {exc}")

    print("\n=== 5. Full plan ===")
    async with XkdrStationProvider(DEMO_API_KEY, city="Delhi") as provider:
        async with OsrmRouter() as router:
            service = PlanService(provider, router)
            plan = await service.plan(
                PlanRequest(
                    origin=ORIGIN,
                    destination=DESTINATION,
                    activity=Activity.WALK,
                    sensitivity=Sensitivity.NORMAL,
                ),
                at=DEMO_MOMENT,
            )
    print(f"  {len(plan.routes)} ranked route(s), source={plan.data_source}")
    for i, route in enumerate(plan.routes, 1):
        print(
            f"  {i}. {route.distance_m:.0f} m  mean_aqi={route.score.mean_aqi:.1f}  "
            f"dose={route.score.dose:.0f}  {route.score.category.value}  "
            f"advice={route.score.advisory.value}  conf={route.aqi_confidence.value}"
        )
        print(f"     unscored fraction: {route.score.unscored_fraction:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
