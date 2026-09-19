"""The daily token-budget circuit breaker's Redis counter (PRD §10.3; m8b task-05 brief,
Interfaces block).

`check_and_would_exceed` decides whether an LLM call may proceed, WITHOUT touching the counter
itself (`worker/triage.py::TriagePipeline.run` calls it before every LLM call site); `record_tokens`
is the only function that increments it, called after a call succeeds. Both share `core.budget`'s
key format (`budget:{utc_date}`) so `core/services/alerts_read.py::get_stats`'s own fail-open read
of the SAME counter (which must never import `worker` -- import-linter contract) can never drift
from the write side's key shape (`.claude/rules/worker.md`: "check the daily token budget before
every LLM call").
"""

from __future__ import annotations

from redis.asyncio import Redis

from core.budget import BUDGET_COUNTER_TTL_S, budget_key, read_tokens_today


async def check_and_would_exceed(redis: Redis, *, budget: int, would_add_estimate: int = 0) -> bool:
    """`True` when today's counter, plus `would_add_estimate`, is already `>= budget`.

    `budget == 0` means unlimited: always `False` regardless of the counter -- the dev default
    must never accidentally freeze the pipeline. A Redis read failure fails OPEN (never blocks a
    call on an infra hiccup), inherited from `core.budget.read_tokens_today`.

    Args:
        redis: The Redis client the counter is stored on.
        budget: The configured daily token budget; `0` disables the check.
        would_add_estimate: An optional look-ahead added to today's counter before comparing, so
            a call whose own tokens would push the day over the line is blocked too.

    Returns:
        Whether an LLM call should be blocked.
    """
    if budget == 0:
        return False
    today = await read_tokens_today(redis)
    return today + would_add_estimate >= budget


async def record_tokens(redis: Redis, *, tokens: int) -> int:
    """`INCR` today's counter by `tokens` and refresh its expiry, in one pipelined round trip.

    Args:
        redis: The Redis client the counter is stored on.
        tokens: The tokens to add -- a successful call's own
            `usage.input_tokens + usage.output_tokens`.

    Returns:
        The counter's new value after the increment.
    """
    key = budget_key()
    pipe = redis.pipeline()
    pipe.incrby(key, tokens)
    pipe.expire(key, BUDGET_COUNTER_TTL_S)
    total, _ = await pipe.execute()
    return int(total)
