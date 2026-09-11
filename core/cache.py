"""`TTLCache` — the in-process cache seam `api/routes/alerts_read.py` reads list/stats responses
through (PRD §8's 15 s / 60 s cache TTLs; M2 final review, plan defect 12) — m3 task-02.

`RedisTTLCache` (m5 task-05; fix-1 review I2) is the shared-across-processes implementation: it
backs ONLY the worker's 24 h AbuseIPDB reputation cache (`worker/main.py::startup`), under the
wire-key prefix `REDIS_CACHE_KEY_PREFIX`. The api's list/stats cache stays the bounded
`InMemoryTTLCache` — a public GET route must never be able to grow a Redis instance that also
holds the ARQ queue (PRD §10.1). A Redis failure on `get` is a miss and on `set` is a no-op —
`lookup_ip_reputation` can never raise because Redis blinked.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

REDIS_CACHE_KEY_PREFIX = "sentinelbrief:cache:"
"""Wire-key prefix every `RedisTTLCache` key is stored under, distinct from
`worker/tools/ip_reputation.py`'s own `CACHE_KEY_PREFIX` (`"abuseipdb:"`), which nests inside this
one when the reputation cache is Redis-backed (m5 task-05)."""


class TTLCache(Protocol):
    """An async get/set byte cache with a per-entry TTL."""

    async def get(self, key: str) -> bytes | None:
        """Return the cached value for `key`, or `None` on a miss or an expired entry."""
        ...

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        """Store `value` under `key` for `ttl_s` seconds, overwriting any existing entry."""
        ...


class InMemoryTTLCache:
    """A single-process `TTLCache` backed by a plain dict; never shared across workers.

    Bounded by `max_entries` (M2/M3 review, plan finding I3): a public GET route must not be a
    resource sink (PRD §10.1) — without a bound, an unauthenticated caller could mint an unbounded
    number of distinct cache keys (e.g. via undeclared query params) and grow this dict without
    limit until the process OOMs. At capacity, `set` first purges every expired entry; only if the
    store is still full after that does it evict the soonest-`expires_at` survivor.
    """

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_entries: int = 1024
    ) -> None:
        """Build an empty cache.

        Args:
            clock: A zero-arg callable returning the current time, defaulting to
                `time.monotonic`. Injectable so expiry can be driven in tests without a real
                sleep (CONVENTIONS.md §10: the clock is a seam).
            max_entries: The maximum number of distinct keys held at once; must be > 0.

        Raises:
            ValueError: When `max_entries` is not positive.
        """
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, bytes]] = {}

    async def get(self, key: str) -> bytes | None:
        """Return the value stored under `key`, or `None` when absent or expired.

        An expired entry (`clock() >= expires_at`) is evicted as a side effect of this call.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        """Store `value` under `key`, expiring `ttl_s` seconds from now.

        Overwriting an existing key never triggers eviction. A new key at capacity first purges
        every expired entry; if the store is still full after that, the entry with the soonest
        `expires_at` is evicted to make room.

        Args:
            key: The cache key.
            value: The bytes to store.
            ttl_s: Seconds until expiry; must be > 0.

        Raises:
            ValueError: When `ttl_s` is not positive.
        """
        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        if key not in self._entries and len(self._entries) >= self._max_entries:
            now = self._clock()
            expired_keys = [k for k, (expires_at, _) in self._entries.items() if now >= expires_at]
            for expired_key in expired_keys:
                del self._entries[expired_key]
            if len(self._entries) >= self._max_entries:
                soonest_key = min(self._entries, key=lambda k: self._entries[k][0])
                del self._entries[soonest_key]
        self._entries[key] = (self._clock() + ttl_s, value)


class RedisTTLCache:
    """A `TTLCache` backed by `redis.asyncio.Redis`, shared across processes (m5 task-05).

    `get`/`set` never raise: a dead Redis makes `get` answer a miss and `set` a no-op, each logged
    exactly once at WARNING with the failing key and the exception class — never the connection
    URL/password (the client already carries that). This is what lets the api's list/stats cache
    and the worker's AbuseIPDB reputation cache survive Redis blinking without a 500 or a raise.
    """

    def __init__(self, redis: Redis, *, key_prefix: str = REDIS_CACHE_KEY_PREFIX) -> None:
        """Build the cache over an already-configured `Redis`/`ArqRedis` client.

        Args:
            redis: The client to issue `GET`/`SET` through; the caller owns its lifecycle.
            key_prefix: Prepended to every key on the wire (defaults to
                `REDIS_CACHE_KEY_PREFIX`; a distinct prefix separates keyspaces in tests).
        """
        self._redis = redis
        self._key_prefix = key_prefix

    async def get(self, key: str) -> bytes | None:
        """Return the cached value for `key`, or `None` on a miss, expiry, or a Redis failure."""
        try:
            value: bytes | None = await self._redis.get(self._key_prefix + key)
            return value
        except (RedisError, OSError) as exc:
            logger.warning("redis cache get failed key=%s exc=%s", key, type(exc).__name__)
            return None

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        """Store `value` under `key` for `ttl_s` seconds; a Redis failure is a logged no-op.

        Args:
            key: The cache key (prefixed with `key_prefix` on the wire).
            value: The bytes to store.
            ttl_s: Seconds until expiry; must be > 0 (parity with `InMemoryTTLCache` — a
                programming error, not a Redis one, so this check runs before any Redis call).

        Raises:
            ValueError: When `ttl_s` is not positive.
        """
        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        try:
            await self._redis.set(self._key_prefix + key, value, px=ttl_s * 1000)
        except (RedisError, OSError) as exc:
            logger.warning("redis cache set failed key=%s exc=%s", key, type(exc).__name__)
            return
