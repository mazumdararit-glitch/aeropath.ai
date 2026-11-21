"""Tests for database models and relationships."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from aeropath.models.geo import AqGridCell, StationReading
from aeropath.models.place import AlertConfig, SavedPlace
from aeropath.models.planner import PlannerAuditLog
from aeropath.models.user import Profile, User


class TestUserAndProfileModels:
    def test_user_instantiation(self) -> None:
        user_id = uuid.uuid4()
        user = User(
            id=user_id,
            auth_id="firebase_user_123",
            email="runner@aeropath.in",
            language="en",
        )
        assert user.id == user_id
        assert user.auth_id == "firebase_user_123"
        assert user.language == "en"

    def test_profile_instantiation(self) -> None:
        user_id = uuid.uuid4()
        profile = Profile(
            id=uuid.uuid4(),
            user_id=user_id,
            age_group="30-45",
            activity_type="run",
            pace_kmh=10.5,
            sensitivity="sensitive",
        )
        assert profile.user_id == user_id
        assert profile.activity_type == "run"
        assert profile.sensitivity == "sensitive"


class TestPlaceAndAlertModels:
    def test_saved_place_instantiation(self) -> None:
        place = SavedPlace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="Rabindra Sarobar",
            lat=22.5126,
            lng=88.3582,
            alert_threshold=150.0,
            alerts_enabled=True,
        )
        assert place.name == "Rabindra Sarobar"
        assert place.alert_threshold == 150.0

    def test_alert_config_instantiation(self) -> None:
        alert = AlertConfig(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            lat=22.5126,
            lng=88.3582,
            threshold_aqi=120.0,
            is_active=True,
        )
        assert alert.threshold_aqi == 120.0
        assert alert.is_active is True


class TestGeoModels:
    def test_aq_grid_cell_instantiation(self) -> None:
        now = datetime.now(UTC)
        cell = AqGridCell(
            h3_index="8861892557fffff",
            hour=now,
            base_aqi=135.0,
            pollutants={"PM2.5": 45.0, "PM10": 90.0},
            source="open_meteo",
            fetched_at=now,
        )
        assert cell.h3_index == "8861892557fffff"
        assert cell.base_aqi == 135.0

    def test_station_reading_instantiation(self) -> None:
        now = datetime.now(UTC)
        reading = StationReading(
            station_id="site_5126",
            station_name="Rabindra Sarobar, Kolkata",
            lat=22.5126,
            lng=88.3582,
            hour=now,
            aqi=115.0,
            pollutants={"PM2.5": 42.0},
            fetched_at=now,
        )
        assert reading.station_id == "site_5126"
        assert reading.aqi == 115.0


class TestPlannerAuditModel:
    def test_planner_audit_log_instantiation(self) -> None:
        log = PlannerAuditLog(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            query_text="easy 4km morning walk",
            parsed_params={"distance_km": 4.0, "activity": "walk"},
            latency_ms=1200,
            outcome="success",
        )
        assert log.query_text == "easy 4km morning walk"
        assert log.outcome == "success"
