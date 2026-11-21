"""Route request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from aeropath.schemas.common import (
    Activity,
    Advisory,
    AQCategory,
    Confidence,
    Coordinate,
    Sensitivity,
)


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
    geometry: list[Coordinate] = Field(
        default_factory=list,
        description="Coordinates along the route polyline for map rendering.",
    )
    is_national_aqi: bool = Field(
        description=(
            "False when the index rests on fewer than CPCB's three required "
            "pollutants. The number is then a particulate sub-index, not a "
            "national AQI, and must be labelled as such in any UI."
        )
    )
    pollutants_used: int = Field(
        description="How many pollutants the index was computed from."
    )


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
    data_date: str | None = Field(
        default=None,
        description=(
            "The day the measurements describe. CPCB ingestion lags, so this is "
            "usually older than generated_at. Never assume they match."
        ),
    )
    data_note: str = Field(
        default="",
        description="Caveats that apply to every number in this response.",
    )
