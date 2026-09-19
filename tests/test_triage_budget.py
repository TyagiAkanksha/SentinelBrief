"""Pins `TriagePipeline`'s daily-token-budget circuit breaker (PRD §10.3; m8b task-05 brief,
Interfaces block: "worker/triage.py: before each LLM call, if check_and_would_exceed(...) → raise
BudgetExceededError; after each success, record_tokens(usage.input+output)"). The budget check
runs BEFORE the underlying LLM call, so an exhausted budget never spends a token; a successful
call still records its own usage afterward. `worker.budget` does not exist yet, so every test
below fails RED today with `ModuleNotFoundError`.

Every test drives the real `TriagePipeline.run` with `tests.fakes.FakeLLMClient` (the only LLM
double, CONVENTIONS.md §10) over the dedicated test Redis (`arq_redis`); no DB session is needed
-- the tool-less pipeline's `run` accepts `session=None`.

Judgment call (recorded in the test-author report): the brief's Interfaces block does not name
`TriagePipeline`'s new constructor parameters for the redis client / the budget value. This file
pins `redis=` and `daily_token_budget=`, mirroring the existing `Settings.daily_token_budget`
field name and the codebase's own `redis=` keyword (`api.factory.create_app`,
`worker.budget.check_and_would_exceed`/`record_tokens`).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from arq.connections import ArqRedis

from core.errors import BudgetExceededError
from tests.fakes import FakeLLMClient
from tests.helpers import VALID4, minimal_alert
from worker.budget import record_tokens
from worker.triage import TriagePipeline


def _today_key() -> str:
    return f"budget:{datetime.now(UTC).date().isoformat()}"


async def test_budget_exhausted_raises_before_any_llm_call(arq_redis: ArqRedis) -> None:
    await record_tokens(arq_redis, tokens=100)
    fake = FakeLLMClient([VALID4])
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        redis=arq_redis,
        daily_token_budget=100,
    )

    with pytest.raises(BudgetExceededError) as excinfo:
        await pipeline.run(minimal_alert())

    assert fake.calls == []  # the LLM was never touched
    assert excinfo.value.tokens_today == 100
    assert excinfo.value.budget == 100


async def test_budget_zero_is_unlimited_and_runs_normally_even_over_a_recorded_count(
    arq_redis: ArqRedis,
) -> None:
    await record_tokens(arq_redis, tokens=999_999)
    fake = FakeLLMClient([VALID4])
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        redis=arq_redis,
        daily_token_budget=0,
    )

    outcome = await pipeline.run(minimal_alert())

    assert outcome.verdict.category == "successful_intrusion"
    assert len(fake.calls) == 1


async def test_successful_run_increments_the_token_counter_by_input_plus_output(
    arq_redis: ArqRedis,
) -> None:
    # FakeLLMClient's default usage is 100 input + 50 output tokens = 150.
    fake = FakeLLMClient([VALID4])
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        redis=arq_redis,
        daily_token_budget=1000,
    )

    await pipeline.run(minimal_alert())

    stored = await arq_redis.get(_today_key())
    assert stored is not None
    assert int(stored) == 150
