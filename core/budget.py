"""Shared key format + fail-open read for the daily token-budget counter (PRD §10.3; m8b
task-05).

`worker/budget.py` owns the counter's writes (`record_tokens`) and the pre-call block decision
(`check_and_would_exceed`); this module owns only the `budget:{utc_date}` key format and a
fail-open READ (`read_tokens_today`), so `core/services/alerts_read.py::get_stats` can read
today's count for `StatsOut.budget_exhausted`/`StatsOut.tokens_today` without importing `worker`
(import-linter contract "core never imports api, worker or evals").
"""

from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

BUDGET_COUNTER_TTL_S = 172_800
"""~2 days -- comfortably outlives one UTC day so the counter is never read after its own key has
already expired, while never accumulating stale per-day keys forever (mirrors
`api/routes/admin.py::_RETRIAGE_COUNTER_TTL_S`)."""


def budget_key() -> str:
    """Today's UTC-date-scoped counter key (the brief's "Reset semantics": `budget:{utc_date}`,
    `utc_date` an ISO-8601 date) -- a new UTC day starts a fresh counter with no cleanup step."""
    return f"budget:{datetime.now(UTC).date().isoformat()}"


async def read_tokens_today(redis: Redis | None) -> int:
    """Today's recorded token count, or `0` when Redis is unwired or unreachable.

    Fails OPEN on both an unwired (`None`) and an unreachable Redis -- mirrors
    `api/deps.py::rate_limit`'s own established unwired/unreachable-Redis fail-open contract
    (m8b task-04): a Redis hiccup must never turn into a spurious `budget_exhausted=True` reading.

    Args:
        redis: The Redis client the counter is stored on, or `None` when unwired.

    Returns:
        The counter's current value, or `0` on a miss, an unwired client, or a Redis failure.
    """
    if redis is None:
        return 0
    try:
        raw = await redis.get(budget_key())
    except (RedisError, OSError):
        return 0
    return int(raw) if raw is not None else 0
