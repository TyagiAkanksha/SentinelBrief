"""Pins `worker/jobs.py::triage_alert_job` — the ARQ job function (PRD §6.1 step 3, §6.2;
m5 task-01 brief, Interfaces block).

A job enqueued through `core.queue.enqueue_triage` and drained by a real, `burst=True` ARQ
`Worker` (never a hand-rolled call to `triage_alert_job`, except the one malformed-id test that
is explicitly "direct call, no worker") must: triage a `pending` alert exactly as
`TriagePipeline.triage_alert` would (this task keeps M2's one-attempt semantics — task-02 adds
retries, and the failure test's own docstring says so); answer `"missing"` without a retry for an
alert id that does not exist, logging the id at WARNING; and reject a malformed alert id with a
plain `ValueError` before ever touching `ctx` (a malformed id can only come from a bug in our own
`enqueue_triage`, never from real input — CONVENTIONS.md §4).

Every DB-touching test uses the throwaway-schema fixtures; every Redis-touching test uses
`arq_redis`/`redis_url` (the dedicated test Redis, flushed).
"""

from __future__ import annotations

import logging
import uuid

import pytest
from arq.connections import ArqRedis
from arq.jobs import Job
from arq.worker import Worker
from arq.worker import func as arq_func
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.errors import LLMCallError
from core.models import AlertRow, VerdictRow
from core.queue import (
    TRIAGE_JOB_NAME,
    TRIAGE_QUEUE_NAME,
    enqueue_triage,
    redis_settings,
    triage_job_id,
)
from tests.fakes import FakeLLMClient
from tests.helpers import seed_alert
from worker.jobs import triage_alert_job
from worker.triage import TriagePipeline

# Severity-4 `successful_intrusion`, escalate=true — duplicated from `tests/test_tool_wiring_and
# _retry_trace.py`'s `VALID4` per the brief (test files never import from each other).
VALID4 = (
    '{"severity": 4, "category": "successful_intrusion", "confidence": 0.9, '
    '"reasoning": "attacker logged in as root and ran reconnaissance commands", '
    '"recommended_action": "isolate host and rotate credentials", "escalate": true}'
)


async def test_enqueued_job_triages_the_alert_through_a_burst_worker(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    alert_id = await seed_alert(db_session, "alert4")
    await db_session.commit()

    queued = await enqueue_triage(arq_redis, alert_id)
    assert queued is True

    fake = FakeLLMClient([VALID4])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")
    worker = Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        ctx={"pipeline": pipeline, "session_factory": db_session_factory},
    )

    await worker.main()
    await worker.close()

    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "triaged"
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 1
    assert worker.jobs_complete == 1
    assert len(fake.calls) == 1  # one complete_structured call, no tools
    assert fake.calls[0].tools is None

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    assert await job.result() == "triaged"
    assert await arq_redis.zcard(TRIAGE_QUEUE_NAME) == 0


async def test_job_for_a_missing_alert_returns_missing_without_retry(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alert_id = uuid.uuid4()
    await enqueue_triage(arq_redis, alert_id)

    pipeline = TriagePipeline(llm=FakeLLMClient([]), model="fake-model", prompt_version="triage-v1")
    worker = Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        ctx={"pipeline": pipeline, "session_factory": db_session_factory},
    )

    with caplog.at_level(logging.WARNING):
        await worker.main()
    await worker.close()

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    assert await job.result() == "missing"
    assert worker.jobs_complete == 1
    assert worker.jobs_retried == 0

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(alert_id) in warnings[0].getMessage()


async def test_job_failure_marks_the_alert_failed_after_max_tries(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    """m5 task-02 re-pin: `worker/retry.py` lands, and an `LLMCallError` now retries with
    backoff instead of failing on the first try (task-01's one-attempt semantics are gone). Three
    scripted `LLMCallError`s exhaust `TRIAGE_JOB_MAX_TRIES=3` (the first run plus two retries);
    the alert is marked `failed` in its own transaction on the last try and the job itself still
    completes normally (`jobs_complete`, not `jobs_failed` — only the alert's status is "failed").
    The full retry-policy arithmetic (backoff values, family-blind retry-vs-fail) is pinned once,
    in `tests/test_retry_policy.py`; this test only proves the job wires that policy in through a
    real, draining `Worker`.
    """
    alert_id = await seed_alert(db_session, "alert4")
    await db_session.commit()

    await enqueue_triage(arq_redis, alert_id)

    pipeline = TriagePipeline(
        llm=FakeLLMClient([LLMCallError("boom"), LLMCallError("boom"), LLMCallError("boom")]),
        model="fake-model",
        prompt_version="triage-v1",
    )
    settings = Settings(
        triage_job_max_tries=3, triage_job_backoff_base_s=0.01, triage_job_backoff_max_s=0.05
    )
    worker = Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        max_tries=3,
        ctx={"pipeline": pipeline, "session_factory": db_session_factory, "settings": settings},
    )

    await worker.main()
    await worker.close()

    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "failed"
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 0
    assert worker.jobs_retried == 2
    assert worker.jobs_complete == 1
    assert worker.jobs_failed == 0  # the job itself completed; only the alert's status is "failed"

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    assert await job.result() == "failed"


async def test_job_rejects_a_malformed_alert_id() -> None:
    with pytest.raises(ValueError):
        await triage_alert_job({"pipeline": None, "session_factory": None}, "not-a-uuid")
