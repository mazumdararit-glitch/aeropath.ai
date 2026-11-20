"""Shared value types for AEROPATH.

Nothing here performs I/O. These types are used by the scoring engine, the
API schemas and the Phase 0 validation harness, so they must stay importable
without a database, a network or a clock.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AQCategory(StrEnum):
    """CPCB National Air Quality Index bands (``ind_cpcb``)."""

    GOOD = "good"
    SATISFACTORY = "satisfactory"
    MODERATE = "moderate"
    POOR = "poor"
    VERY_POOR = "very_poor"
    SEVERE = "severe"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    HIGH = "high"


class Activity(StrEnum):
    WALK = "walk"
    RUN = "run"


class Advisory(StrEnum):
    """What the user should do, given their sensitivity.

    Deliberately independent of the category. The category is a fact about the
    air; the advisory is advice about the person. Recolouring the map per user
    would misreport the air quality, so advice rides alongside instead.
    """

    OK = "ok"
    CAUTION = "caution"
    AVOID = "avoid"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RoadClass(StrEnum):
    """OpenStreetMap ``highway`` values, collapsed to bands that share a factor."""

    MOTORWAY = "motorway"
    TRUNK = "trunk"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    RESIDENTIAL = "residential"
    LIVING_STREET = "living_street"
    PEDESTRIAN = "pedestrian"
    SERVICE = "service"
    FOOTWAY = "footway"
    PATH = "path"
    CYCLEWAY = "cycleway"
    UNKNOWN = "unknown"


class Coordinate(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Freshness(BaseModel):
    """Data-age envelope attached to every response that touches grid data.

    FR9 requires cached data to be visibly timestamped, and PRD 10.6.8 requires
    a lowered confidence on provider fallback. Both live here so that no new
    endpoint can silently forget them.
    """

    data_age_seconds: int = Field(ge=0)
    source: str
    ingested_at: datetime
    is_stale: bool
    confidence: Confidence


class TimeWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    mean_aqi: float = Field(ge=0)
    dose: float = Field(ge=0)
    category: AQCategory

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: datetime, info: object) -> datetime:
        start = getattr(info, "data", {}).get("start")
        if start is not None and v < start:
            raise ValueError("end must not precede start")
        return v


class SegmentGeometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinates: tuple[Coordinate, ...]


class Waypoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    coordinate: Coordinate
