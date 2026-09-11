"""Pins `core.cache.RedisTTLCache` — a `TTLCache` (m3 task-02's Protocol) backed by
`redis.asyncio.Redis`, under the wire-key prefix `REDIS_CACHE_KEY_PREFIX`
(`"sentinelbrief:cache:"`) — PRD §4 (Redis 7 for queue/cache/pubsub), §6.3's 24 h reputation
cache and §8's list/stats cache both share this one implementation from m5 task-05.

Every test drives the real dedicated test Redis (`arq_redis`, m5 task-01's fixture) for the live
branches, and `core.queue.make_redis` pointed at a port nothing listens on (`127.0.0.1:1`) for
the "Redis is dead" branches — never a mock of our own code (CONVENTIONS.md §10). `get`/`set`
against a dead Redis must never raise: a `get` is a miss and a `set` is a no-op, each logged
exactly once at WARNING with the failing key and the exception class, never the connection
URL/password (the dead-Redis URL below carries a fake password, `cache-pw-3`, that must never
leak into a log line).
"""

from __future__ import annotations

import logging

import pytest
from arq.connections import ArqRedis

from core.cache import REDIS_CACHE_KEY_PREFIX, RedisTTLCache
from core.queue import make_redis

# Port 1 (tcpmux) has nothing listening on a dev/CI box, so the connection is refused
# immediately instead of hanging on a timeout. The password is fake and distinguishable
# ("cache-pw-3") so a leak into a log line is unambiguous.
_DEAD_REDIS_URL = "redis://:cache-pw-3@127.0.0.1:1/0"


async def test_set_then_get_under_the_prefix_with_a_pttl(arq_redis: ArqRedis) -> None:
    cache = RedisTTLCache(arq_redis)

    await cache.set("k", b"v", 15)

    assert await cache.get("k") == b"v"
    assert await arq_redis.get(REDIS_CACHE_KEY_PREFIX + "k") == b"v"
    pttl = await arq_redis.pttl(REDIS_CACHE_KEY_PREFIX + "k")
    assert 0 < pttl <= 15_000


async def test_get_miss_is_none(arq_redis: ArqRedis) -> None:
    cache = RedisTTLCache(arq_redis)

    assert await cache.get("does-not-exist") is None


async def test_set_overwrites_and_resets_the_ttl(arq_redis: ArqRedis) -> None:
    cache = RedisTTLCache(arq_redis)

    await cache.set("k", b"a", 5)
    await cache.set("k", b"b", 30)

    assert await cache.get("k") == b"b"
    pttl = await arq_redis.pttl(REDIS_CACHE_KEY_PREFIX + "k")
    assert pttl > 5_000


async def test_nonpositive_ttl_is_a_value_error(arq_redis: ArqRedis) -> None:
    cache = RedisTTLCache(arq_redis)

    with pytest.raises(ValueError):
        await cache.set("k", b"v", 0)


async def test_get_against_a_dead_redis_is_a_miss_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dead = make_redis(_DEAD_REDIS_URL, socket_timeout_s=0.5)
    try:
        cache = RedisTTLCache(dead)

        with caplog.at_level(logging.WARNING):
            result = await cache.get("k")

        assert result is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "key=k" in message
        assert "exc=" in message
        assert "cache-pw-3" not in caplog.text
    finally:
        await dead.aclose()


async def test_set_against_a_dead_redis_is_a_noop_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dead = make_redis(_DEAD_REDIS_URL, socket_timeout_s=0.5)
    try:
        cache = RedisTTLCache(dead)

        with caplog.at_level(logging.WARNING):
            await cache.set("k2", b"v", 5)  # a different key than the get test above (rule 5)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "key=k2" in message
        assert "exc=" in message
        assert "cache-pw-3" not in caplog.text
    finally:
        await dead.aclose()


async def test_custom_key_prefix(arq_redis: ArqRedis) -> None:
    cache = RedisTTLCache(arq_redis, key_prefix="t:")

    await cache.set("k", b"v", 5)

    assert await arq_redis.get("t:k") == b"v"
