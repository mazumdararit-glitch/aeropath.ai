"""Factory function to instantiate configured GridProvider or FallbackProviderChain."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aeropath.config import Settings
from aeropath.external.bias_corrected_provider import StationCorrectedProvider
from aeropath.external.google_aq_client import GoogleAirQualityProvider
from aeropath.external.open_meteo_provider import OpenMeteoProvider
from aeropath.external.provider import GridProvider
from aeropath.external.station_providers import (
    GroundStationReading,
    OpenAQClient,
    WaqiClient,
)
from aeropath.external.xkdr_provider import XkdrStationProvider
from aeropath.services.provider_chain import FallbackProviderChain


def build_provider(settings: Settings) -> GridProvider:
    """Construct the configured grid provider or automatic fallback chain.

    Fallback order per Task A4:
    1. Google Air Quality (when google_aq_key is set)
    2. Station-corrected Open-Meteo (CAMS + ground truth anchoring)
    3. Plain Open-Meteo (CAMS baseline)
    """
    if settings.grid_provider == "xkdr":
        key = settings.require_provider_key()
        assert key is not None
        return XkdrStationProvider(key)

    if not settings.enable_provider_chain:
        if settings.google_aq_key and settings.grid_provider == "google_aq":
            return GoogleAirQualityProvider(settings.google_aq_key)
        return OpenMeteoProvider()

    chain: list[GridProvider] = []

    # 1. Google Air Quality (if configured)
    if settings.google_aq_key:
        chain.append(GoogleAirQualityProvider(settings.google_aq_key))

    # 2. Station-corrected Open-Meteo
    station_loader: (
        Callable[[], list[GroundStationReading] | Awaitable[list[GroundStationReading]]] | None
    ) = None
    if settings.waqi_token:
        waqi = WaqiClient(token=settings.waqi_token)

        async def _load_waqi() -> list[GroundStationReading]:
            return await waqi.fetch_in_bbox(
                min_lat=28.4,
                min_lng=76.8,
                max_lat=28.9,
                max_lng=77.5,
            )

        station_loader = _load_waqi
    elif settings.openaq_api_key:
        openaq = OpenAQClient(api_key=settings.openaq_api_key)

        async def _load_openaq() -> list[GroundStationReading]:
            return await openaq.fetch_in_bbox(
                min_lat=28.4,
                min_lng=76.8,
                max_lat=28.9,
                max_lng=77.5,
            )

        station_loader = _load_openaq

    station_corrected = StationCorrectedProvider(
        OpenMeteoProvider(),
        stations_loader=station_loader,
        shrinkage_factor=settings.bias_correction_shrinkage,
        max_reading_age_h=settings.max_station_reading_age_h,
    )
    chain.append(station_corrected)

    # 3. Plain Open-Meteo
    chain.append(OpenMeteoProvider())

    return FallbackProviderChain(
        chain,
        request_budget_limit=settings.google_aq_request_budget,
        cache_ttl_s=settings.provider_cache_ttl_s,
    )
