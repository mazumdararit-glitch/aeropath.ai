"""Fallback provider chain with budget control and caching (Sprint 3 - Task A4).

Implements automatic fallback:
Google Air Quality (if configured) -> Station-corrected Open-Meteo -> Plain Open-Meteo.
Includes an in-memory TTL coordinate cache and request budget tracking.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from aeropath.external.provider import GridProvider, PointReading
from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)


class FallbackProviderChain:
    """Dispatches concentration requests across an ordered chain of providers.

    If a provider fails or its request budget is exhausted, it falls back
    transparently to the next provider in the chain.
    """

    def __init__(
        self,
        providers: list[GridProvider],
        *,
        request_budget_limit: int = 1000,
        cache_ttl_s: int = 1800,
    ) -> None:
        if not providers:
            raise ValueError("FallbackProviderChain requires at least one provider")
        self.providers = providers
        self.request_budget_limit = request_budget_limit
        self.cache_ttl_s = cache_ttl_s

        self.requests_made = 0
        self.active_provider_name: str = providers[0].name
        self.name: str = f"chain({self.active_provider_name})"
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self._last_data_age: datetime | None = None

        # Cache key: (lat_round, lng_round) -> (timestamp, PointReading)
        self._cache: dict[tuple[float, float], tuple[float, PointReading]] = {}

    async def __aenter__(self) -> FallbackProviderChain:
        for p in self.providers:
            if hasattr(p, "__aenter__"):
                await p.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        for p in self.providers:
            if hasattr(p, "__aexit__"):
                await p.__aexit__(*exc_info)

    async def aclose(self) -> None:
        for p in self.providers:
            if hasattr(p, "aclose"):
                await p.aclose()

    def _round_coord(self, c: Coordinate) -> tuple[float, float]:
        """Round coordinates to 3 decimals (~110m) for spatial caching."""
        return (round(c.lat, 3), round(c.lng, 3))

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        """Fetch concentrations for points with cache check and chain fallback."""
        if not points:
            return []

        now_ts = time.time()
        results: dict[int, PointReading] = {}
        uncached_indices: list[int] = []
        uncached_coords: list[Coordinate] = []

        # 1. Check in-memory cache
        for idx, pt in enumerate(points):
            k = self._round_coord(pt)
            if k in self._cache:
                cached_time, reading = self._cache[k]
                if now_ts - cached_time < self.cache_ttl_s:
                    results[idx] = reading
                    continue
            uncached_indices.append(idx)
            uncached_coords.append(pt)

        if not uncached_coords:
            return [results[i] for i in range(len(points))]

        # 2. Iterate through providers in order
        last_exc: Exception | None = None
        for provider in self.providers:
            # Check budget limit for primary provider
            if provider == self.providers[0] and self.requests_made >= self.request_budget_limit:
                logger.warning(
                    "Request budget reached for %s (%d/%d); falling back",
                    provider.name,
                    self.requests_made,
                    self.request_budget_limit,
                )
                continue

            try:
                readings = await provider.concentrations_at(uncached_coords, at=at)
                if not readings or all(not r.concentrations for r in readings):
                    raise RuntimeError(f"Provider {provider.name} returned empty concentrations")

                self.active_provider_name = provider.name
                self.last_success_at = datetime.now(UTC)
                if provider == self.providers[0]:
                    self.requests_made += 1

                for idx, r in zip(uncached_indices, readings, strict=False):
                    results[idx] = r
                    self._cache[self._round_coord(r.coordinate)] = (now_ts, r)
                    if r.observed_at:
                        self._last_data_age = r.observed_at

                return [results[i] for i in range(len(points))]

            except Exception as exc:
                last_exc = exc
                self.last_error = f"{provider.name}: {exc}"
                self.last_error_at = datetime.now(UTC)
                logger.warning(
                    "Provider %s failed (%s); trying next provider in chain",
                    provider.name,
                    exc,
                )

        if last_exc:
            raise RuntimeError(f"All providers in fallback chain failed: {last_exc}") from last_exc
        raise RuntimeError("All providers in fallback chain failed or skipped")

    def get_health_status(self) -> dict[str, Any]:
        """Return diagnostic health metrics for the provider chain."""
        remaining = max(0, self.request_budget_limit - self.requests_made)
        return {
            "status": "degraded" if self.last_error else "healthy",
            "active_provider": self.active_provider_name,
            "fallback_chain": [p.name for p in self.providers],
            "data_age": self._last_data_age.isoformat() if self._last_data_age else None,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "request_budget": {
                "limit": self.request_budget_limit,
                "used": self.requests_made,
                "remaining": remaining,
            },
        }
