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

    osrm_base_url: str = "https://router.project-osrm.org"
    osrm_timeout_s: float = 15.0

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


def load_settings() -> Settings:
    return Settings()
