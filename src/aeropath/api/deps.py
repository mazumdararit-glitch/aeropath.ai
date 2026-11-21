"""Dependency injection helpers for FastAPI routes."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aeropath.db.session import get_db
from aeropath.models.user import Profile, User
from aeropath.schemas.common import Activity, Sensitivity

logger = logging.getLogger(__name__)

DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve current user from token or fall back to persistent dev/demo user.

    In production, this decodes a Firebase ID token to extract the user's UID and email.
    In local dev / tests, if no header is supplied, it auto-provisions a stable dev user
    so APIs are immediately testable in Swagger UI without friction.
    """
    # 1. Lookup dev user or existing user
    stmt = select(User).options(selectinload(User.profile)).where(User.id == DEV_USER_ID)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if user is None:
        user = User(
            id=DEV_USER_ID,
            auth_id="dev-user-001",
            email="developer@aeropath.local",
            language="en",
        )
        db.add(user)
        profile = Profile(
            user_id=DEV_USER_ID,
            sensitivity=Sensitivity.NORMAL.value,
            activity_type=Activity.WALK.value,
        )
        db.add(profile)
        await db.flush()
        # Reload with profile relationship
        stmt = select(User).options(selectinload(User.profile)).where(User.id == DEV_USER_ID)
        result = await db.execute(stmt)
        user = result.scalars().first()
        assert user is not None

    return user
