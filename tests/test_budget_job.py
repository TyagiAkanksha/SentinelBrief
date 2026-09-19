"""Pins controller ruling R-M8b-3 (m8b task-05 dispatch): a `BudgetExceededError` raised inside
`TriagePipeline.triage_attempt` is a DEFER, not a failure. `worker/jobs.py::triage_alert_job`
must never route it through the terminal-`failed` write `decide_retry`'s family-blind policy
gives every OTHER exception on the last allowed `TRIAGE_JOB_MAX_TRIES` try (PRD §6.2) -- the
alert stays `pending` at every try count, including the last one, and a plain re-run after the
Redis counter clears (simulating the UTC-midnight reset) triages it normally, consuming none of
the normal retry budget along the way.

The ruling explicitly leaves the ARQ mechanism unpinned ("do not pin a specific arq mechanism"):
this file asserts only the observable DB state, tolerating either an `arq.worker.Retry` or a
plain return from the direct `triage_alert_job(ctx, ...)` call (the established direct-call
pattern from `tests/test_worker_job_retry.py`, e.g. its `test_cancellation_propagates_...`).

`worker.budget` does not exist yet, so every test below fails RED today with
`ModuleNotFoundError`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from arq.connections import ArqRedis
from arq.worker import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.models import AlertRow
from tests.fakes import FakeLLMClient
from tests.helpers import VALID4, seed_alert
from worker.budget import record_tokens
from worker.jobs import triage_alert_job
from worker.triage import TriagePipeline


def _today_key() -> str:
    return f"budget:{datetime.now(UTC).date().isoformat()}"


def _job_settings() -> Settings:
    return Settings(
        triage_job_max_tries=3, triage_job_backoff_base_s=0.0, triage_job_backoff_max_s=0.0
    )


async def _call_tolerating_a_retry(ctx: dict[str, Any], alert_id: str) -> str | None:
    """Call the job directly; a deferred retry may surface as `arq.worker.Retry` OR as a plain
    return -- the ruling leaves the mechanism to the implementer, so this helper tolerates either
    and lets the caller assert on DB state instead."""
    try:
        return await triage_alert_job(ctx, alert_id)
    except Retry:
        return None


async def test_budget_exceeded_never_marks_the_alert_failed_even_on_the_last_try(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="budget-defer")
    await db_session.commit()
    await record_tokens(arq_redis, tokens=100)  # already at the budget before any attempt

    fake = FakeLLMClient([])  # must never be touched: the budget check blocks before any LLM call
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        redis=arq_redis,
        daily_token_budget=100,
    )
    settings = _job_settings()

    for job_try in (1, 2, 3):  # 3 == triage_job_max_tries: decide_retry's family-blind "fail" line
        ctx: dict[str, Any] = {
            "pipeline": pipeline,
            "session_factory": db_session_factory,
            "settings": settings,
            "job_id": "triage:test",
            "job_try": job_try,
            "redis": arq_redis,
        }
        await _call_tolerating_a_retry(ctx, str(alert_id))

        async with db_session_factory() as fresh:
            row = await fresh.get(AlertRow, alert_id)
            assert row is not None
            assert row.status == "pending", f"job_try={job_try} must never mark the alert failed"

    assert fake.calls == []


async def test_budget_exceeded_alert_resumes_once_the_counter_clears(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="budget-resume")
    await db_session.commit()
    await record_tokens(arq_redis, tokens=100)

    fake = FakeLLMClient([VALID4])
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        redis=arq_redis,
        daily_token_budget=100,
    )
    settings = _job_settings()
    ctx: dict[str, Any] = {
        "pipeline": pipeline,
        "session_factory": db_session_factory,
        "settings": settings,
        "job_id": "triage:test",
        "job_try": 3,  # the last allowed try -- still must not fail terminally
        "redis": arq_redis,
    }

    await _call_tolerating_a_retry(ctx, str(alert_id))

    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "pending"
    assert fake.calls == []  # the exhausted attempt above never touched the LLM

    await arq_redis.delete(_today_key())  # simulate the UTC-midnight reset

    result = await _call_tolerating_a_retry({**ctx, "job_try": 1}, str(alert_id))

    assert result == "triaged"
    assert len(fake.calls) == 1
    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "triaged"
