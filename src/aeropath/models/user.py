"""User and profile SQLAlchemy models."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aeropath.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from aeropath.models.place import AlertConfig, SavedPlace


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    auth_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
        doc="Firebase or Supabase UID",
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    profile: Mapped[Profile | None] = relationship(
        "Profile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    saved_places: Mapped[list[SavedPlace]] = relationship(
        "SavedPlace",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    alerts: Mapped[list[AlertConfig]] = relationship(
        "AlertConfig",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    age_group: Mapped[str | None] = mapped_column(String(20), nullable=True)
    activity_type: Mapped[str] = mapped_column(String(20), default="walk", nullable=False)
    pace_kmh: Mapped[float | None] = mapped_column(nullable=True)
    sensitivity: Mapped[str] = mapped_column(
        String(20),
        default="normal",
        nullable=False,
        doc="normal | sensitive | high",
    )

    user: Mapped[User] = relationship("User", back_populates="profile")


Index("idx_users_auth_id", User.auth_id)
