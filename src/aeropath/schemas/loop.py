"""Pydantic schemas for Circular Loop Generation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeropath.schemas.common import Activity, Coordinate, Sensitivity


class LoopRequest(BaseModel):
    origin: Coordinate
    target_distance_km: float = Field(default=5.0, ge=0.5, le=30.0)
    activity: Activity = Activity.RUN
    sensitivity: Sensitivity = Sensitivity.NORMAL
    num_alternatives: int = Field(default=3, ge=1, le=5)
