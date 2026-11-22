"""Air quality query endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from aeropath.config import Settings, load_settings
from aeropath.domain import cpcb_aqi
from aeropath.domain.cpcb_aqi import InsufficientDataError
from aeropath.external.provider import GridProvider
from aeropath.schemas.airquality import AirQualityPointResponse
from aeropath.schemas.common import Coordinate, Sensitivity
from aeropath.services.provider_factory import build_provider
from aeropath.services.scoring import categories

router = APIRouter(prefix="/v1/airquality", tags=["airquality"])


def get_grid_provider(settings: Annotated[Settings, Depends(load_settings)]) -> GridProvider:
    return build_provider(settings)


@router.get("", response_model=AirQualityPointResponse)
async def get_current_airquality(
    lat: Annotated[float, Query(ge=-90.0, le=90.0, description="Latitude")],
    lng: Annotated[float, Query(ge=-180.0, le=180.0, description="Longitude")],
    provider: Annotated[GridProvider, Depends(get_grid_provider)],
    sensitivity: Annotated[
        Sensitivity,
        Query(description="User sensitivity level for personalized advisory"),
    ] = Sensitivity.NORMAL,
) -> AirQualityPointResponse:
    """Fetch current real-time air quality at a specific coordinate."""
    coord = Coordinate(lat=lat, lng=lng)
    now = datetime.now(UTC)

    readings = await provider.concentrations_at([coord], at=now)
    if not readings or not readings[0].concentrations:
        raise HTTPException(
            status_code=503,
            detail="Air quality data is currently unavailable for this coordinate.",
        )

    reading = readings[0]
    try:
        res = cpcb_aqi.compute(reading.concentrations, require_minimum=False)
    except InsufficientDataError as exc:
        raise HTTPException(
            status_code=503,
            detail="Insufficient pollutant concentrations reported for AQI calculation.",
        ) from exc

    dominant = res.dominant_pollutant or "unknown"
    pollutants_map = {sub.pollutant: sub.sub_index for sub in res.sub_indices}
    advisory = categories.advisory(res.category, sensitivity)

    averaging_map = {
        pollutant: str(hrs) for pollutant, hrs in (reading.averaging_hours or {}).items()
    }

    return AirQualityPointResponse(
        coordinate=coord,
        aqi=res.aqi,
        category=res.category,
        dominant_pollutant=dominant,
        pollutants=pollutants_map,
        advisory=advisory,
        confidence=reading.confidence,
        is_national_aqi=res.is_national,
        recorded_at=reading.observed_at or now,
        source=provider.name,
        averaging_period=averaging_map,
        is_estimated=reading.is_estimated,
    )
