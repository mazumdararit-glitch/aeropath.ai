"""Caching layer for AEROPATH.

Provides async Redis caching with transparent fallback to in-memory TTL caching
when Redis is unavailable or unconfigured. This ensures local dev and tests run
seamlessly without requiring a running Redis daemon.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import Any

from aeropath.config import Settings, load_settings

logger = logging.getLogger(__name__)


class CacheClient:
    """Unified cache interface with fallback."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._memory_cache: dict[str, tuple[float, str]] = {}
        self._redis: Any = None
        self._tried_redis = False

    async def _get_redis(self) -> Any:
        if self._tried_redis:
            return self._redis
        self._tried_redis = True
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(
                self.settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=1.0,
            )
            await r.ping()
            self._redis = r
            logger.info("Connected to Redis cache at %s", self.settings.redis_url)
        except Exception as exc:
            logger.info("Redis unavailable (%s), falling back to in-memory cache.", exc)
            self._redis = None
        return self._redis

    async def get(self, key: str) -> str | None:
        """Get value by key."""
        r = await self._get_redis()
        if r is not None:
            try:
                val: str | None = await r.get(key)
                return val
            except Exception as exc:
                logger.debug("Redis get error for key %s: %s", key, exc)

        # In-memory lookup
        now = time.time()
        entry = self._memory_cache.get(key)
        if entry is not None:
            expires_at, val = entry
            if now < expires_at:
                return val
            del self._memory_cache[key]
        return None

    async def set(self, key: str, value: str, ttl_seconds: int = 3600) -> None:
        """Set key with TTL."""
        r = await self._get_redis()
        if r is not None:
            try:
                await r.setex(key, ttl_seconds, value)
                return
            except Exception as exc:
                logger.debug("Redis set error for key %s: %s", key, exc)

        now = time.time()
        self._memory_cache[key] = (now + ttl_seconds, value)

    async def get_json(self, key: str) -> Any | None:
        """Get and deserialize JSON."""
        raw = await self.get(key)
        if raw is None:
            return None
        with contextlib.suppress(Exception):
            return json.loads(raw)
        return None

    async def set_json(self, key: str, obj: Any, ttl_seconds: int = 3600) -> None:
        """Serialize and set JSON."""
        await self.set(key, json.dumps(obj), ttl_seconds)

    async def delete(self, key: str) -> None:
        """Evict key."""
        r = await self._get_redis()
        if r is not None:
            with contextlib.suppress(Exception):
                await r.delete(key)
        self._memory_cache.pop(key, None)

    async def aclose(self) -> None:
        """Close Redis connection if open."""
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None
            self._tried_redis = False


_default_cache: CacheClient | None = None


def get_cache() -> CacheClient:
    """Singleton cache accessor."""
    global _default_cache
    if _default_cache is None:
        _default_cache = CacheClient()
    return _default_cache
