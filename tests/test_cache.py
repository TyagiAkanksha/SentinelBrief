"""Pins `core/cache.py`'s `TTLCache` protocol and `InMemoryTTLCache`, plus the cache seam threaded
through `create_app`/`api/deps.py::get_cache` (PRD §8's 15 s / 60 s cache TTLs; M2 final review,
plan defect 12) — m3 task-02.

`InMemoryTTLCache` takes an injectable `clock` (CONVENTIONS.md §10: the clock is a seam), so
expiry-boundary behavior is exercised without a real sleep — every test here drives a mutable
one-element list the test itself advances, never `time.sleep`. `get_cache`'s and `create_app`'s
default-cache wiring are pinned too: a route reaching `Depends(get_cache)` with no cache installed
must never be a silent `AttributeError` at request time.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.factory import create_app
from core.cache import InMemoryTTLCache


async def test_in_memory_cache_miss_returns_none() -> None:
    cache = InMemoryTTLCache()

    assert await cache.get("missing") is None


async def test_in_memory_cache_hit_before_expiry() -> None:
    box = [0.0]
    cache = InMemoryTTLCache(clock=lambda: box[0])
    await cache.set("k", b"v", 15)

    box[0] = 14.0

    assert await cache.get("k") == b"v"


async def test_in_memory_cache_expires_at_ttl_boundary() -> None:
    box = [0.0]
    cache = InMemoryTTLCache(clock=lambda: box[0])
    await cache.set("k", b"v", 15)

    box[0] = 15.0

    assert await cache.get("k") is None
    # The boundary miss evicts the entry itself — a later `set` is not required to clear it.
    # Asserted directly against `_entries`, the internal shape `core/cache.py`'s produced
    # interface documents (`dict[str, tuple[float, bytes]]`).
    assert "k" not in cache._entries


async def test_in_memory_cache_set_overwrites_value_and_ttl() -> None:
    box = [0.0]
    cache = InMemoryTTLCache(clock=lambda: box[0])
    await cache.set("k", b"v1", 5)

    box[0] = 4.0
    await cache.set("k", b"v2", 10)  # restarts the TTL: expires at 4 + 10 = 14, not the old 5

    box[0] = 9.0
    assert await cache.get("k") == b"v2"  # the original 5s TTL would already have expired here

    box[0] = 14.0
    assert await cache.get("k") is None


async def test_in_memory_cache_rejects_non_positive_ttl() -> None:
    cache = InMemoryTTLCache()

    with pytest.raises(ValueError):
        await cache.set("k", b"v", 0)
    with pytest.raises(ValueError):
        await cache.set("k", b"v", -1)


def test_create_app_installs_in_memory_cache_by_default() -> None:
    app = create_app()

    assert isinstance(app.state.cache, InMemoryTTLCache)


async def test_get_cache_returns_injected_instance() -> None:
    # `get_cache`/`TTLCache` are imported lazily, inside the one test that needs them, the same
    # way `tests/conftest.py`'s DB fixtures lazily import `core.db`/`alembic`: this file must stay
    # collectible before `api/deps.py::get_cache` exists (the module-level failure this task's
    # RED evidence pins is `core.cache` itself, not this name).
    from fastapi import Depends

    from api.deps import get_cache
    from core.cache import TTLCache

    injected = InMemoryTTLCache()
    app = create_app(cache=injected)

    async def _probe(cache: TTLCache = Depends(get_cache)) -> dict[str, int]:
        return {"cache_id": id(cache)}

    app.add_api_route("/_probe/cache", _probe, methods=["GET"], operation_id="probe_cache")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_probe/cache")

    assert response.status_code == 200
    assert response.json() == {"cache_id": id(injected)}
