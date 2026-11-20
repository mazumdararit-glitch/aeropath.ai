"""Live probe: coverage discovery and a full plan against the current dataset.

    python scratch/live_probe.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aeropath.external.cpcb_stations import StationClient  # noqa: E402
from aeropath.external.osrm import OsrmRouter  # noqa: E402
from aeropath.external.xkdr_provider import XkdrStationProvider  # noqa: E402
from aeropath.schemas.common import Activity, Coordinate, Sensitivity  # noqa: E402
from aeropath.services.planning import (  # noqa: E402
    DataUnavailableError,
    PlanRequest,
    PlanService,
)


def _key() -> str:
    env = REPO_ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("XKDR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("XKDR_API_KEY not found in .env")


ORIGIN = Coordinate(lat=28.6315, lng=77.2167)
DESTINATION = Coordinate(lat=28.6129, lng=77.2295)


async def main() -> int:
    key = _key()

    print("=== 1. dataset coverage ===")
    async with StationClient(api_key=key) as client:
        window = await client.coverage()
        print(f"  first_month : {window.first_month}")
        print(f"  last_month  : {window.last_month}")
        print(f"  exported_at : {window.exported_at}")

        print("\n=== 2. newest day with actual rows (Delhi) ===")
        day = await client.latest_available_day(city="Delhi")
        print(f"  resolved: {day}")

    print("\n=== 3. full plan, no explicit date ===")
    async with XkdrStationProvider(key, city="Delhi") as provider:
        async with OsrmRouter() as router:
            service = PlanService(provider, router)
            try:
                plan = await service.plan(
                    PlanRequest(
                        origin=ORIGIN,
                        destination=DESTINATION,
                        activity=Activity.WALK,
                        sensitivity=Sensitivity.NORMAL,
                    )
                )
            except DataUnavailableError as exc:
                print(f"  DATA UNAVAILABLE: {exc}")
                return 1

    print(f"  data_date   : {plan.data_date}")
    print(f"  generated_at: {plan.generated_at.isoformat()}")
    print(f"  routes      : {len(plan.routes)}")
    for i, route in enumerate(plan.routes, 1):
        print(
            f"  {i}. {route.distance_m:6.0f} m  aqi={route.score.mean_aqi:6.1f}  "
            f"dose={route.score.dose:10.0f}  {route.score.category.value:11} "
            f"advice={route.score.advisory.value:8} conf={route.aqi_confidence.value}"
        )
    print(f"  unscored fraction: {plan.routes[0].score.unscored_fraction:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
