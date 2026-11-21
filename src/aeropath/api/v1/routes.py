"""Routing endpoints including closed circular loop generation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from aeropath.config import Settings, load_settings
from aeropath.external.osrm import OsrmRouter, RoutingError
from aeropath.external.provider import GridProvider
from aeropath.schemas.loop import LoopRequest
from aeropath.schemas.routes import RouteModel
from aeropath.services.loop_generator import LoopGenerator
from aeropath.services.provider_factory import build_provider

router = APIRouter(prefix="/v1/routes", tags=["routes"])


def get_loop_generator(
    settings: Annotated[Settings, Depends(load_settings)],
) -> LoopGenerator:
    provider: GridProvider = build_provider(settings)
    router_inst = OsrmRouter(settings.osrm_base_url, timeout_s=settings.osrm_timeout_s)
    return LoopGenerator(provider, router_inst)


@router.post("/loop", response_model=list[RouteModel])
async def generate_loops(
    body: LoopRequest,
    generator: Annotated[LoopGenerator, Depends(get_loop_generator)],
) -> list[RouteModel]:
    """Generate scenic circular loops around origin matching a target distance."""
    try:
        routes = await generator.generate_loops(body, at=datetime.now(UTC))
    except RoutingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not routes:
        raise HTTPException(
            status_code=404,
            detail="Could not construct circular loops matching the target distance in this area.",
        )

    return [
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
            geometry=list(route.geometry),
            is_national_aqi=route.is_national_aqi,
            pollutants_used=route.pollutants_used,
        )
        for route in routes
    ]
