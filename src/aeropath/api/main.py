"""HTTP surface for AEROPATH.

Deliberately thin. The route-planning logic lives in ``services.planning`` and the
maths in ``services.scoring``, so this module only translates JSON to a request,
calls the service, and translates the answer back. Anything resembling a
calculation here would be a bug: it would be untested by the scoring suite.

Startup is where configuration fails, not the first request. A missing XKDR key
is a deployment mistake and should say so immediately, with the link to fix it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from aeropath.config import Settings, load_settings
from aeropath.external.osrm import OsrmRouter, RoutingError
from aeropath.external.xkdr_provider import XkdrStationProvider
from aeropath.schemas.common import (
    Activity,
    Advisory,
    AQCategory,
    Confidence,
    Coordinate,
    Sensitivity,
)
from aeropath.services.planning import PlanRequest, PlanService

logger = logging.getLogger(__name__)


class PlanRequestModel(BaseModel):
    origin: Coordinate
    destination: Coordinate
    activity: Activity = Activity.WALK
    sensitivity: Sensitivity = Sensitivity.NORMAL
    alternate_routes: int = Field(default=2, ge=0, le=3)


class RouteModel(BaseModel):
    distance_m: float
    duration_s: float
    mean_aqi: float
    dose: float
    category: AQCategory
    advisory: Advisory
    confidence: Confidence
    basis: str
    unscored_fraction: float


class WindowModel(BaseModel):
    hour: int
    mean_aqi: float
    category: AQCategory
    dose: float
    samples: int
    basis: str


class PlanResponse(BaseModel):
    routes: list[RouteModel]
    best_windows: list[WindowModel]
    generated_at: datetime
    data_source: str


def create_app(
    settings: Settings | None = None, service: PlanService | None = None
) -> FastAPI:
    """Build the app.

    ``service`` exists so tests can inject a stub without reaching into FastAPI's
    dependency graph, which is an implementation detail that changes between
    versions.
    """
    resolved = settings or load_settings()

    app = FastAPI(
        title="AEROPATH",
        description="Route and time planner that ranks outdoor activity by air pollution exposure.",
        version="0.1.0",
    )

    def get_service() -> PlanService:
        if service is not None:
            return service
        # The key is required; failing here rather than per-request keeps the
        # error at startup where an operator will actually see it.
        provider = XkdrStationProvider(resolved.require_xkdr())
        router = OsrmRouter(
            resolved.osrm_base_url, timeout_s=resolved.osrm_timeout_s
        )
        return PlanService(provider, router)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "xkdr_configured": bool(resolved.xkdr_api_key),
            "router": resolved.osrm_base_url,
        }

    @app.post("/v1/plan", response_model=PlanResponse)
    async def plan(
        body: PlanRequestModel,
        # A default-value dependency rather than ``Annotated[...]``: this module
        # uses ``from __future__ import annotations``, so annotations are strings
        # and FastAPI cannot resolve a closure named in one. The default value is
        # a real object, which it can.
        svc: PlanService = Depends(get_service),  # noqa: B008
    ) -> PlanResponse:
        request = PlanRequest(
            origin=body.origin,
            destination=body.destination,
            activity=body.activity,
            sensitivity=body.sensitivity,
            alternate_routes=body.alternate_routes,
        )
        try:
            result = await svc.plan(request, at=datetime.now(UTC))
        except RoutingError as exc:
            # The router failed, which is upstream and not the caller's fault.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return PlanResponse(
            routes=[
                RouteModel(
                    distance_m=route.distance_m,
                    duration_s=route.duration_s,
                    mean_aqi=route.score.mean_aqi,
                    dose=route.score.dose,
                    category=route.score.category,
                    advisory=route.score.advisory,
                    confidence=route.aqi_confidence,
                    basis=route.basis,
                    unscored_fraction=route.score.unscored_fraction,
                )
                for route in result.routes
            ],
            best_windows=[
                WindowModel(
                    hour=window.hour,
                    mean_aqi=window.mean_aqi,
                    category=window.category,
                    dose=window.dose,
                    samples=window.samples,
                    basis=window.basis,
                )
                for window in result.best_windows
            ],
            generated_at=result.generated_at,
            data_source=result.data_source,
        )

    return app


app = create_app()
