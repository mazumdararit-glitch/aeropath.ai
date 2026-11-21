"""Factory function to instantiate configured GridProvider."""

from __future__ import annotations

from aeropath.config import Settings
from aeropath.external.open_meteo_provider import OpenMeteoProvider
from aeropath.external.provider import GridProvider
from aeropath.external.xkdr_provider import XkdrStationProvider


def build_provider(settings: Settings) -> GridProvider:
    """Construct the configured grid provider.

    Kept as a dedicated factory so the choice is testable and isolated from API routers.
    """
    key = settings.require_provider_key()
    if settings.grid_provider == "xkdr":
        assert key is not None
        return XkdrStationProvider(key)
    return OpenMeteoProvider()
