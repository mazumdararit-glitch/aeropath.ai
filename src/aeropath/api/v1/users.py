"""User profile management endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aeropath.api.deps import get_current_user
from aeropath.db.session import get_db
from aeropath.models.user import User
from aeropath.schemas.user import ProfileResponse, ProfileUpdateRequest

router = APIRouter(prefix="/v1/profile", tags=["users"])


@router.get("", response_model=ProfileResponse)
async def get_profile(
    user: Annotated[User, Depends(get_current_user)],
) -> ProfileResponse:
    """Fetch current user profile and preferences."""
    profile = user.profile
    return ProfileResponse(
        user_id=user.id,
        email=user.email,
        sensitivity=profile.sensitivity if profile else "normal",  # type: ignore[arg-type]
        default_activity=profile.activity_type if profile else "walk",  # type: ignore[arg-type]
        created_at=user.created_at or datetime.now(UTC),
    )


@router.put("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProfileResponse:
    """Update user sensitivity, default activity, or notification token."""
    profile = user.profile
    if profile is not None:
        if body.sensitivity is not None:
            profile.sensitivity = body.sensitivity.value
        if body.default_activity is not None:
            profile.activity_type = body.default_activity.value
        await db.flush()
        await db.commit()

    return ProfileResponse(
        user_id=user.id,
        email=user.email,
        sensitivity=profile.sensitivity if profile else "normal",  # type: ignore[arg-type]
        default_activity=profile.activity_type if profile else "walk",  # type: ignore[arg-type]
        created_at=user.created_at or datetime.now(UTC),
    )
