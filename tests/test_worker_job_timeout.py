"""New file (m5 fix wave, review N-I1 shape (a), "Pins (test-author)" (3)): pins the inner
attempt deadline `worker/jobs.py::triage_alert_job` bounds `pipeline.triage_attempt` with —
`asyncio.wait_for(..., timeout=settings.triage_attempt_timeout_s)` — strictly BELOW ARQ's own
outer `job_timeout`. A hung LLM call becomes a retryable builtin `TimeoutError` the job's own
`except Exception` boundary sees (`decide_retry` retries it, `reason=TimeoutError`, then marks the
alert `failed` on the last try), instead of ARQ cancelling the whole job from OUTSIDE with no
retry and no terminal write — the alert would otherwise rest `pending` forever (the gap the final
review's probe P2 reproduced live: `jobs_failed=1 jobs_retried=0`, `status='pending'`).

RED at HEAD: `core.config.Settings` has no `triage_attempt_timeout_s` field (silently ignored,
`extra="ignore"`) and `worker/jobs.py` wraps `pipeline.triage_attempt` in no `wait_for` at all, so
the 10 s sleep this file scripts is bounded only by ARQ's own outer `job_timeout=5` — the job
fails once with NO retry and the alert rests `pending`, not `failed`.

The burst-`Worker`/retry-settings pattern is copied from `tests/test_worker_job_retry.py::_worker`
(pinned by another author) rather than imported, per the repo's own convention that test files
never import from each other; this file's own `_worker` adds the explicit `job_timeout=` the
others never needed to vary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from arq.connections import ArqRedis
from arq.worker import Worker
from arq.worker import func as arq_func
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.models import AlertRow, VerdictRow
from core.queue import TRIAGE_JOB_NAME, TRIAGE_QUEUE_NAME, enqueue_triage, redis_settings
from core.services.alerts import get_alert_for_update
from tests.fakes import FakeLLMClient
from tests.helpers import seed_alert
from worker.jobs import triage_alert_job
from worker.triage import TriagePipeline


class _SleepingLLMClient(FakeLLMClient):
    """A `FakeLLMClient` whose `complete_structured` never returns inside the inner attempt
    deadline this file pins — the one behavior the base fake cannot script (it always returns or
    raises immediately, never hangs)."""

    def __init__(self) -> None:
        super().__init__([])

    async def complete_structured(self, *, messages, response_model, model):
        await asyncio.sleep(10)
        raise AssertionError("unreachable: the inner attempt deadline must cancel this first")


def _worker(
    *,
    redis_url: str,
    session_factory: Any,
    settings: Settings,
    llm: FakeLLMClient,
    job_timeout: float,
) -> Worker:
    """The burst `Worker` pattern from `tests/test_worker_job_retry.py::_worker`, copied (not
    imported) with an explicit `job_timeout=` — this file needs ARQ's OUTER deadline set distinctly
    from `Settings.triage_attempt_timeout_s`'s INNER one, which no other file in the suite varies.
    Never carries `"redis"` in `ctx` — ARQ installs that key itself before the first job."""
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    return Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        max_tries=3,
        job_timeout=job_timeout,
        ctx={"pipeline": pipeline, "session_factory": session_factory, "settings": settings},
    )


async def test_attempt_exceeding_the_inner_deadline_retries_then_fails(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """m5 fix wave (review N-I1 shape (a)): the inner `asyncio.wait_for` bounds each attempt well
    below ARQ's own `job_timeout`, so a hung LLM call retries (twice) then fails terminally —
    exactly the existing retry/fail policy, never an unretried ARQ-external job failure. Mutant:
    drop the `wait_for` -> `jobs_failed == 1` and the alert rests `pending`."""
    alert_id = await seed_alert(db_session, "alert4", session_id="attempt-inner-timeout")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    settings = Settings(
        triage_job_max_tries=3,
        triage_job_backoff_base_s=0.0,
        triage_job_backoff_max_s=0.05,
        triage_attempt_timeout_s=0.2,
        triage_job_timeout_s=5,
    )
    worker = _worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=settings,
        llm=_SleepingLLMClient(),
        job_timeout=5,
    )

    with caplog.at_level(logging.WARNING):
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
    assert worker.jobs_failed == 0

    retry_warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "triage job retry" in r.getMessage()
    ]
    assert len(retry_warnings) == 2
    assert all("reason=TimeoutError" in m for m in retry_warnings)

    # The inner `wait_for`'s cancellation still hit `triage_attempt`'s own rollback: the lock is
    # released promptly, not held for the full 10 s sleep (m5 fix wave, review N-M1's same shape).
    async with db_session_factory() as lock_check_session:
        locked_row = await asyncio.wait_for(
            get_alert_for_update(lock_check_session, alert_id), timeout=2.0
        )
        assert locked_row.id == alert_id
        await lock_check_session.commit()
