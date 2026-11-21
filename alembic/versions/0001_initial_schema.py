"""Initial database schema.

Revision ID: 0001
Revises:
Create Date: 2026-11-21 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("auth_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("language", sa.String(10), server_default="en", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_users_auth_id", "users", ["auth_id"], unique=True)
    op.create_index("ix_users_email", "users", ["email"])

    # 2. profiles
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("age_group", sa.String(20), nullable=True),
        sa.Column("activity_type", sa.String(20), server_default="walk", nullable=False),
        sa.Column("pace_kmh", sa.Float(), nullable=True),
        sa.Column("sensitivity", sa.String(20), server_default="normal", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    # 3. saved_places
    op.create_table(
        "saved_places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("alert_threshold", sa.Float(), nullable=True),
        sa.Column("alerts_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_saved_places_user", "saved_places", ["user_id"])

    # 4. alert_configs
    op.create_table(
        "alert_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "place_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("saved_places.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("threshold_aqi", sa.Float(), server_default="100.0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_alert_configs_user_active", "alert_configs", ["user_id", "is_active"])

    # 5. aq_grid
    op.create_table(
        "aq_grid",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("h3_index", sa.String(20), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base_aqi", sa.Float(), nullable=False),
        sa.Column("pollutants", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(50), server_default="open_meteo", nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_aq_grid_cell_hour", "aq_grid", ["h3_index", "hour"])

    # 6. station_readings
    op.create_table(
        "station_readings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("station_id", sa.String(64), nullable=False),
        sa.Column("station_name", sa.String(128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aqi", sa.Float(), nullable=False),
        sa.Column("pollutants", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_station_readings_station_hour", "station_readings", ["station_id", "hour"])

    # 7. planner_audit_logs
    op.create_table(
        "planner_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("parsed_params", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(20), server_default="success", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_planner_audit_logs_user_id", "planner_audit_logs", ["user_id"])


def downgrade() -> None:
    op.drop_table("planner_audit_logs")
    op.drop_table("station_readings")
    op.drop_table("aq_grid")
    op.drop_table("alert_configs")
    op.drop_table("saved_places")
    op.drop_table("profiles")
    op.drop_table("users")
