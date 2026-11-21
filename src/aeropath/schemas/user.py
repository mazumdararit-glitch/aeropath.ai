"""Pydantic schemas for User Profile and Preferences."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from aeropath.schemas.common import Activity, Sensitivity


class ProfileUpdateRequest(BaseModel):
    sensitivity: Sensitivity | None = None
    default_activity: Activity | None = None
    fcm_token: str | None = None


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str | None
    sensitivity: Sensitivity
    default_activity: Activity
    created_at: datetime
