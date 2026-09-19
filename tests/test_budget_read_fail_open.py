"""Pins `core/budget.py::read_tokens_today`'s fail-OPEN behavior against an UNREACHABLE Redis
(m8b whole-repo review, finding t05 M3: this branch was untested — `tests/test_stats_budget.py::
test_stats_fails_open_when_redis_is_unwired` only covers the `redis=None` half of the contract,
never a Redis that is wired but errors on `GET`).

Mirrors `tests/test_rate_limit.py::test_redis_down_fails_open_get_still_succeeds`'s own dead-Redis
pattern (a real, unreachable `ArqRedis` client — port 1: nothing listens, so the connection is
refused immediately rather than timing out) instead of a mock of our own code (CONVENTIONS.md
§10), per the task brief's "mirror the `rate_limit` fail-open test pattern."
"""

from __future__ import annotations

from core.budget import read_tokens_today
from core.queue import make_redis

# Port 1 (tcpmux) has nothing listening on a dev/CI box, so the connection is refused immediately
# instead of hanging on a timeout — same as tests/test_rate_limit.py's `_DEAD_REDIS_URL`.
_DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


async def test_read_tokens_today_fails_open_when_redis_get_raises() -> None:
    dead_redis = make_redis(_DEAD_REDIS_URL, socket_timeout_s=1.0)
    try:
        tokens = await read_tokens_today(dead_redis)
    finally:
        await dead_redis.aclose()

    # An unreachable Redis must never propagate (or turn into a spurious "budget exhausted"
    # reading) -- fail open with 0 instead, exactly like `read_tokens_today(None)` does.
    assert tokens == 0
