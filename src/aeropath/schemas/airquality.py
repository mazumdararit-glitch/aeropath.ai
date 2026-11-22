"""Pydantic schemas for Air Quality endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from aeropath.schemas.common import (
    Advisory,
    AQCategory,
    Confidence,
    Coordinate,
)


class AirQualityPointResponse(BaseModel):
    coordinate: Coordinate
    aqi: float = Field(description="CPCB National Air Quality Index (0-500 scale)")
    category: AQCategory
    dominant_pollutant: str
    pollutants: dict[str, float] = Field(
        default_factory=dict,
        description="Sub-indices or concentrations per pollutant.",
    )
    advisory: Advisory
    confidence: Confidence
    is_national_aqi: bool
    recorded_at: datetime
    source: str
    averaging_period: dict[str, str] = Field(
        default_factory=dict,
        description="Averaging period per pollutant (e.g. '24h', '8h', '1h-damped').",
    )
    is_estimated: bool = Field(
        default=False,
        description="True if rolling history was insufficient and fallback estimates were used.",
    )
