"""`TTLCache` — the in-process cache seam `api/routes/alerts_read.py` reads list/stats responses
through (PRD §8's 15 s / 60 s cache TTLs; M2 final review, plan defect 12) — m3 task-02.

No project imports: a pure seam M5 swaps for a `RedisTTLCache` beside `InMemoryTTLCache` in one
wiring line (`api/factory.py::create_app`'s `cache=` kwarg). The methods are `async` even though
the in-memory version never awaits — a Redis client is async, and a sync `Protocol` would force
M5 to change the Protocol, both routes, and every route test.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class TTLCache(Protocol):
    """An async get/set byte cache with a per-entry TTL."""

    async def get(self, key: str) -> bytes | None:
        """Return the cached value for `key`, or `None` on a miss or an expired entry."""
        ...

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        """Store `value` under `key` for `ttl_s` seconds, overwriting any existing entry."""
        ...


class InMemoryTTLCache:
    """A single-process `TTLCache` backed by a plain dict; never shared across workers."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Build an empty cache.

        Args:
            clock: A zero-arg callable returning the current time, defaulting to
                `time.monotonic`. Injectable so expiry can be driven in tests without a real
                sleep (CONVENTIONS.md §10: the clock is a seam).
        """
        self._clock = clock
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

        Args:
            key: The cache key.
            value: The bytes to store.
            ttl_s: Seconds until expiry; must be > 0.

        Raises:
            ValueError: When `ttl_s` is not positive.
        """
        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        self._entries[key] = (self._clock() + ttl_s, value)
