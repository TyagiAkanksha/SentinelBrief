"""Pins `worker/budget.py` (new, m8b task-05 brief Interfaces block) -- the daily token-budget
circuit breaker's Redis counter: `check_and_would_exceed` (`budget == 0` never blocks; a counter
at or past the budget, or an estimate that would push it there, does), `record_tokens` (`INCR`
the UTC-date-scoped counter, return the new running total, `EXPIRE` it), and the
`daily_token_budget` `Settings` field the breaker reads (`.claude/rules/worker.md`: "check the
daily token budget before every LLM call").

`worker.budget` does not exist yet, so every Redis-driven test below fails RED today with
`ModuleNotFoundError`; the `Settings`/`BudgetExceededError` tests fail RED with `AttributeError`
(no `daily_token_budget` field, no `BudgetExceededError` class yet).

The counter key is pinned to `budget:{utc_date}` -- the brief's own "Reset semantics" wording,
`utc_date` an ISO-8601 date (`datetime.now(UTC).date().isoformat()`); every test that must prove
a fact about a DIFFERENT day writes that key directly against the dedicated test Redis
(`arq_redis`, flushed before and after by the fixture), never a mock of our own code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from arq.connections import ArqRedis

from core.config import Settings
from core.errors import BudgetExceededError, SentinelBriefError
from worker.budget import check_and_would_exceed, record_tokens


def _today_key() -> str:
    return f"budget:{datetime.now(UTC).date().isoformat()}"


def _yesterday_key() -> str:
    return f"budget:{(datetime.now(UTC).date() - timedelta(days=1)).isoformat()}"


# --- Settings surface --------------------------------------------------------------------------


def test_daily_token_budget_defaults_to_zero_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAILY_TOKEN_BUDGET", raising=False)

    settings = Settings()

    assert settings.daily_token_budget == 0


def test_daily_token_budget_reads_its_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_TOKEN_BUDGET", "5000")

    settings = Settings()

    assert settings.daily_token_budget == 5000


# --- BudgetExceededError -------------------------------------------------------------------------


def test_budget_exceeded_error_is_a_sentinelbrief_error_carrying_tokens_and_budget() -> None:
    exc = BudgetExceededError("daily budget exhausted", tokens_today=120, budget=100)

    assert isinstance(exc, SentinelBriefError)
    assert exc.code == "budget_exceeded"
    assert exc.tokens_today == 120
    assert exc.budget == 100


# --- check_and_would_exceed ----------------------------------------------------------------------


async def test_budget_zero_never_blocks_even_with_a_huge_counter(arq_redis: ArqRedis) -> None:
    await arq_redis.set(_today_key(), 10_000_000)

    assert await check_and_would_exceed(arq_redis, budget=0) is False


async def test_under_budget_does_not_block(arq_redis: ArqRedis) -> None:
    await record_tokens(arq_redis, tokens=99)

    assert await check_and_would_exceed(arq_redis, budget=100) is False


async def test_at_or_over_budget_blocks(arq_redis: ArqRedis) -> None:
    await record_tokens(arq_redis, tokens=100)

    assert await check_and_would_exceed(arq_redis, budget=100) is True


async def test_would_add_estimate_pushes_a_call_over_the_line(arq_redis: ArqRedis) -> None:
    await record_tokens(arq_redis, tokens=8)

    assert await check_and_would_exceed(arq_redis, budget=10, would_add_estimate=1) is False
    assert await check_and_would_exceed(arq_redis, budget=10, would_add_estimate=2) is True


# --- record_tokens --------------------------------------------------------------------------------


async def test_record_tokens_increments_and_returns_the_running_total(
    arq_redis: ArqRedis,
) -> None:
    first_total = await record_tokens(arq_redis, tokens=40)
    second_total = await record_tokens(arq_redis, tokens=10)

    assert first_total == 40
    assert second_total == 50


async def test_record_tokens_is_scoped_to_todays_utc_date_key(arq_redis: ArqRedis) -> None:
    """A stale/different-day counter must never inflate today's total -- the whole point of the
    date-scoped key is that a new UTC day starts a fresh counter (the brief's "Reset semantics")
    with no explicit cleanup step."""
    await arq_redis.set(_yesterday_key(), 999_999)

    total = await record_tokens(arq_redis, tokens=5)

    assert total == 5
    # today's own 5, never yesterday's 999_999 + 5:
    assert await check_and_would_exceed(arq_redis, budget=5) is True
    assert await check_and_would_exceed(arq_redis, budget=6) is False
    assert int(await arq_redis.get(_yesterday_key())) == 999_999  # untouched


async def test_record_tokens_key_carries_an_expiry(arq_redis: ArqRedis) -> None:
    """Reset semantics (brief): "key `budget:{utc_date}` with `EXPIRE` to end-of-day (or a fixed
    26h TTL)" -- a key with no expiry at all would never let a stuck process's counter self-heal.
    """
    await record_tokens(arq_redis, tokens=5)

    ttl = await arq_redis.ttl(_today_key())

    assert ttl > 0
