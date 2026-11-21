"""Air quality grid and station readings models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from aeropath.db.base import Base


class AqGridCell(Base):
    """Precomputed hourly air quality for an H3 cell."""

    __tablename__ = "aq_grid"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    h3_index: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    base_aqi: Mapped[float] = mapped_column(Float, nullable=False)
    pollutants: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="open_meteo", nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StationReading(Base):
    """Ground truth readings from physical monitoring stations (CPCB/WBPCB)."""

    __tablename__ = "station_readings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    station_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    aqi: Mapped[float] = mapped_column(Float, nullable=False)
    pollutants: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("idx_aq_grid_cell_hour", AqGridCell.h3_index, AqGridCell.hour)
Index("idx_station_readings_station_hour", StationReading.station_id, StationReading.hour)
