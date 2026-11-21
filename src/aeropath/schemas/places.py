"""Pydantic schemas for Saved Places and Alerts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    alert_threshold: float | None = Field(default=None, ge=0.0, le=500.0)
    alerts_enabled: bool = True


class PlaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    lat: float
    lng: float
    alert_threshold: float | None
    alerts_enabled: bool
    created_at: datetime


class AlertConfigCreate(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    threshold_aqi: float = Field(default=100.0, ge=0.0, le=500.0)
    place_id: UUID | None = None


class AlertConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lat: float
    lng: float
    threshold_aqi: float
    is_active: bool
    created_at: datetime
