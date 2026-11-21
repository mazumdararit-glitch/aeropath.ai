"""Saved places and AQI alert subscriptions."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aeropath.api.deps import get_current_user
from aeropath.db.session import get_db
from aeropath.models.place import AlertConfig, SavedPlace
from aeropath.models.user import User
from aeropath.schemas.places import (
    AlertConfigCreate,
    AlertConfigResponse,
    PlaceCreate,
    PlaceResponse,
)

places_router = APIRouter(prefix="/v1/places", tags=["places"])
alerts_router = APIRouter(prefix="/v1/alerts", tags=["alerts"])


@places_router.get("", response_model=list[PlaceResponse])
async def list_places(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PlaceResponse]:
    """List all saved places for the authenticated user."""
    stmt = (
        select(SavedPlace)
        .where(SavedPlace.user_id == user.id)
        .order_by(SavedPlace.created_at.desc())
    )
    result = await db.execute(stmt)
    return [PlaceResponse.model_validate(p) for p in result.scalars().all()]


@places_router.post("", response_model=PlaceResponse, status_code=status.HTTP_201_CREATED)
async def create_place(
    body: PlaceCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PlaceResponse:
    """Save a new place (home, office, favorite park) with optional alert threshold."""
    place = SavedPlace(
        user_id=user.id,
        name=body.name,
        lat=body.lat,
        lng=body.lng,
        alert_threshold=body.alert_threshold,
        alerts_enabled=body.alerts_enabled,
    )
    db.add(place)
    await db.flush()
    await db.commit()
    await db.refresh(place)
    return PlaceResponse.model_validate(place)


@places_router.delete("/{place_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_place(
    place_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a saved place and cascade to related alert configs."""
    stmt = select(SavedPlace).where(
        SavedPlace.id == place_id, SavedPlace.user_id == user.id
    )
    result = await db.execute(stmt)
    place = result.scalars().first()
    if place is None:
        raise HTTPException(status_code=404, detail="Saved place not found")

    # Explicitly remove child alert configurations associated with this place
    alerts_stmt = select(AlertConfig).where(
        AlertConfig.place_id == place_id, AlertConfig.user_id == user.id
    )
    alerts_result = await db.execute(alerts_stmt)
    for alert in alerts_result.scalars().all():
        await db.delete(alert)

    await db.delete(place)
    await db.flush()
    await db.commit()


@alerts_router.get("", response_model=list[AlertConfigResponse])
async def list_alerts(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[AlertConfigResponse]:
    """List all active air quality alert subscriptions for the user."""
    stmt = (
        select(AlertConfig)
        .where(AlertConfig.user_id == user.id)
        .order_by(AlertConfig.created_at.desc())
    )
    result = await db.execute(stmt)
    return [AlertConfigResponse.model_validate(a) for a in result.scalars().all()]


@alerts_router.post(
    "", response_model=AlertConfigResponse, status_code=status.HTTP_201_CREATED
)
async def create_alert(
    body: AlertConfigCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertConfigResponse:
    """Subscribe to threshold-based AQI alerts for a point or place."""
    alert = AlertConfig(
        user_id=user.id,
        place_id=body.place_id,
        lat=body.lat,
        lng=body.lng,
        threshold_aqi=body.threshold_aqi,
        is_active=True,
    )
    db.add(alert)
    await db.flush()
    await db.commit()
    await db.refresh(alert)
    return AlertConfigResponse.model_validate(alert)
