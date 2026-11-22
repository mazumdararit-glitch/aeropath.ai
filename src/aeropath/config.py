"""Runtime configuration.

The XKDR key is free and instant, so there is no reason to operate without one:
https://airquality.xkdr.org/signup

Missing configuration is raised at import of the settings object rather than at
the first request, because a 500 from a data path is a much worse error message
than "you have not set XKDR_API_KEY".
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Printed in the failure message so the fix is one link away.
XKDR_SIGNUP_URL = "https://airquality.xkdr.org/signup"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    xkdr_api_key: str = Field(default="", description="XKDR India Air Quality API key")
    xkdr_base_url: str = "https://airquality.xkdr.org"

    # Which grid source the plan endpoint uses.
    #
    # "open_meteo" is the default because it is live. XKDR's public dataset
    # collapsed after 2024-12 (4.1M rows/month -> ~1.4k), so it is a historical
    # archive rather than a live feed.
    #
    # "google_aq" uses Google's 500m street-level air quality grid.
    #
    # "xkdr" uses real station measurements but is months behind, and after
    # 2024-12 reports only PM2.5 and PM10, which is below CPCB's three-pollutant
    # minimum for a national AQI.
    grid_provider: str = "open_meteo"
    google_aq_key: str = Field(
        default="", description="Google Air Quality API key for 500m resolution"
    )

    # Street dispersion model: "rules_v1" (applies road type and rush hour factors)
    # or "identity_v1" (unadjusted base grid)
    street_model_version: str = "rules_v1"

    # ── Pollutant Averaging & Damping Constants (Sprint 1 - A1) ──
    # UNVERIFIED ASSUMPTION (Rule 5): Scaling factor to estimate 8-h rolling mean ozone
    # from 1-h peak solar snapshot when hourly history is absent.
    ozone_damping_factor: float = Field(
        default=0.65,
        description="Unverified assumption: factor scaling 1h ozone to 8h rolling mean.",
    )
    # CPCB completeness: min 16 of 24 hours (66.7%) for 24-h averaging (PM2.5, PM10, NO2, SO2)
    min_completeness_24h: float = Field(
        default=16 / 24,
        description="CPCB guideline: min valid fraction for 24-h averages (16 of 24h).",
    )
    # CPCB completeness: min 6 of 8 hours (75%) for 8-h rolling averages (Ozone, CO)
    min_completeness_8h: float = Field(
        default=6 / 8,
        description="CPCB guideline: min valid fraction for 8-h averages (6 of 8h).",
    )

    # ── Ground-Station Provider & Bias Correction (Sprint 1 - A2) ──
    waqi_token: str = Field(default="", description="WAQI token for live ground station feed")
    openaq_api_key: str = Field(default="", description="OpenAQ v3 API key for station feed")
    max_station_reading_age_h: float = Field(
        default=3.0, description="Max reading age for live ground stations in hours."
    )
    # UNVERIFIED ASSUMPTION (Rule 5): Shrinkage factor applied to station-model residuals
    # to correct satellite model bias without overfitting to isolated station noise.
    bias_correction_shrinkage: float = Field(
        default=0.75,
        description="Unverified assumption: shrinkage applied to station-model residual.",
    )

    # ── Physical Inhaled Dose Model Constants (Sprint 2 - R1) ──
    # UNVERIFIED ASSUMPTION (Rule 5): Minute ventilation rates (Litres/min) by activity
    # Walk: 15 L/min; Run: 45 L/min; Cycle: 30 L/min
    ventilation_l_per_min: dict[str, float] = Field(
        default_factory=lambda: {
            "walk": 15.0,
            "run": 45.0,
            "cycle": 30.0,
        },
        description="Unverified assumption: minute ventilation rate in L/min by activity.",
    )
    # UNVERIFIED ASSUMPTION (Rule 5): Movement speeds (m/s) by activity
    # Walk: 1.34 m/s (4.8 km/h); Run: 2.68 m/s (9.6 km/h); Cycle: 5.0 m/s (18 km/h)
    activity_speeds_mps: dict[str, float] = Field(
        default_factory=lambda: {
            "walk": 1.34,
            "run": 2.68,
            "cycle": 5.0,
        },
        description="Unverified assumption: typical movement speeds in m/s by activity.",
    )

    # Routing backend
    osrm_base_url: str = "https://router.project-osrm.org"
    osrm_timeout_s: float = 15.0

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgrespassword@localhost:5432/aeropath",
        description="Async PostgreSQL + PostGIS connection string",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Cache (Redis)
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for route and grid caching",
    )

    # CORS
    cors_origins: list[str] = []

    # Station readings update hourly; refetching more often than this only adds
    # load to a volunteer-run API.
    cache_ttl_s: int = 900

    # How far back the hour-of-day climatology looks. Long enough to smooth a
    # single unusual week, short enough to track the seasonal shift.
    climatology_days: int = 90

    # Along-route sampling. A 5 m spacing over a 5 km walk is 1000 samples, which
    # is finer than the station spacing can justify.
    segment_sample_m: float = 50.0

    def require_xkdr(self) -> str:
        """The API key, or a startup error that says how to get one."""
        if not self.xkdr_api_key:
            raise RuntimeError(
                "XKDR_API_KEY is not set. It is free and instant: "
                f"{XKDR_SIGNUP_URL} — then add XKDR_API_KEY=... to .env"
            )
        return self.xkdr_api_key

    def require_provider_key(self) -> str | None:
        """The key the selected provider needs, if any.

        Only the XKDR provider needs one. Open-Meteo is keyless, so the default
        deployment has no required configuration at all.
        """
        if self.grid_provider == "xkdr":
            return self.require_xkdr()
        if self.grid_provider == "open_meteo":
            return None
        raise RuntimeError(
            f"unknown GRID_PROVIDER {self.grid_provider!r}; "
            "expected 'open_meteo' or 'xkdr'"
        )


def load_settings() -> Settings:
    return Settings()
