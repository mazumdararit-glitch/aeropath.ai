"""Tests for the caching client."""

import pytest

from aeropath.utils.cache import CacheClient


@pytest.mark.asyncio
async def test_in_memory_cache_fallback() -> None:
    cache = CacheClient()
    # Ensure redis is not used by forcing None
    cache._tried_redis = True
    cache._redis = None

    await cache.set("test_key", "hello_world", ttl_seconds=10)
    val = await cache.get("test_key")
    assert val == "hello_world"

    await cache.set_json("json_key", {"aqi": 120, "category": "poor"}, ttl_seconds=10)
    data = await cache.get_json("json_key")
    assert data == {"aqi": 120, "category": "poor"}

    await cache.delete("test_key")
    assert await cache.get("test_key") is None
