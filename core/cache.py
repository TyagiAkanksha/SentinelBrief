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
