"""Pins `worker/jobs.py::triage_alert_job`'s retry-aware shape: one attempt per ARQ try, backoff
below the last try, terminal `failed` on it (any exception family except `NotFoundError`), the
poison-alert queue-draining guarantee, and the `verdict.created` publish after a successful commit
(PRD §6.2, §8; m5 task-02 brief, Interfaces block, "Interfaces -> test table").

Every test that drains a real queue builds the burst `Worker` exactly as `tests/test_worker_job.py`
does (task-01's pattern), with `ctx["settings"]` carrying the three new retry settings and
`max_tries=3` passed to the `Worker` too, so ARQ's own belt-and-braces cap matches the job's own
policy. Every retry test uses `triage_job_backoff_base_s=0.01` (deferred retries settle in
milliseconds — the real numbers are pinned once, in `tests/test_retry_policy.py`, never re-proven
here by sleeping). The one exception is the poison test, which uses `base_s=0.0` and `max_jobs=1`
so ARQ's queue order stays deterministic (see its own docstring). Tests never put `"redis"` in the
`ctx` they hand a `Worker` — ARQ installs that key itself before the first job; only the two tests
that call `triage_alert_job` directly put a fake Redis under that key.

Every DB-touching test uses the throwaway-schema fixtures; every Redis-touching test uses
`arq_redis`/`redis_url` (the dedicated test Redis, flushed).
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import time
import uuid
from typing import Any

import pytest
import redis.exceptions
from arq.connections import ArqRedis
from arq.jobs import Job
from arq.worker import Worker
from arq.worker import func as arq_func
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.db import make_engine, make_session_factory
from core.errors import LLMCallError
from core.models import AlertRow, VerdictRow
from core.queue import (
    TRIAGE_JOB_NAME,
    TRIAGE_QUEUE_NAME,
    VERDICT_CREATED_CHANNEL,
    enqueue_triage,
    redis_settings,
    triage_job_id,
)
from core.schemas.alerts_read import reasoning_excerpt
from tests.fakes import FakeLLMClient
from tests.helpers import seed_alert
from worker.jobs import triage_alert_job
from worker.triage import TriagePipeline

# Severity-4 `successful_intrusion`, escalate=true — duplicated from `tests/test_worker_job.py`
# per the repo's own convention (test files never import from each other).
VALID4 = (
    '{"severity": 4, "category": "successful_intrusion", "confidence": 0.9, '
    '"reasoning": "attacker logged in as root and ran reconnaissance commands", '
    '"recommended_action": "isolate host and rotate credentials", "escalate": true}'
)


def _retry_settings(*, base_s: float = 0.01, max_s: float = 0.05, max_tries: int = 3) -> Settings:
    """The retry `Settings` every test in this file threads into `ctx["settings"]`."""
    return Settings(
        triage_job_max_tries=max_tries,
        triage_job_backoff_base_s=base_s,
        triage_job_backoff_max_s=max_s,
    )


def _worker(
    *,
    redis_url: str,
    session_factory: Any,
    settings: Settings,
    llm: FakeLLMClient,
    max_jobs: int | None = None,
) -> Worker:
    """The burst `Worker` pattern from `tests/test_worker_job.py`, plus the retry-era `ctx`
    entries (`"settings"`) and `max_tries` this task adds. Never carries `"redis"` — ARQ sets it."""
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    kwargs: dict[str, Any] = {}
    if max_jobs is not None:
        kwargs["max_jobs"] = max_jobs
    return Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        max_tries=3,
        ctx={"pipeline": pipeline, "session_factory": session_factory, "settings": settings},
        **kwargs,
    )


async def _drain_pubsub(pubsub: Any, *, timeout_total_s: float = 5.0) -> list[str]:
    """Collect every already-queued `message["data"]` off `pubsub`, bounded to
    `timeout_total_s` total (m5 task-02 brief, resolution 3)."""
    messages: list[str] = []
    deadline = time.monotonic() + timeout_total_s
    while time.monotonic() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if message is None:
            break
        messages.append(message["data"])
    return messages


async def test_llm_call_errors_retry_with_backoff_then_succeed(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="retry-then-succeed")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    fake = FakeLLMClient([LLMCallError("a"), LLMCallError("b"), VALID4])
    worker = _worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_retry_settings(),
        llm=fake,
    )

    with caplog.at_level(logging.WARNING):
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
    assert worker.jobs_retried == 2
    assert worker.jobs_complete == 1
    assert len(fake.calls) == 3

    retry_warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "triage job retry" in r.getMessage()
    ]
    assert len(retry_warnings) == 2
    assert any(
        "try=1/3" in m and "defer_s=0.0" in m and "reason=llm_call_failed" in m
        for m in retry_warnings
    )
    assert any(
        "try=2/3" in m and "defer_s=0.0" in m and "reason=llm_call_failed" in m
        for m in retry_warnings
    )


async def test_poison_alert_fails_after_max_tries_and_the_queue_keeps_draining(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    """Ordering assumption (rule 9): ARQ hands runnable jobs out in ascending queue-score order,
    and a `Retry(defer=0)` leaves the retried job's score unchanged (arq/worker.py: `incr_score`
    stays 0 when `e.defer_score` is falsy) — so a job enqueued earlier keeps a strictly lower
    score than one enqueued later, even after being retried. With `max_jobs=1` there is only ever
    one runnable slot, so alert A's three attempts (job_try 1-3, each re-queued at its own
    unchanged, still-lower score) all run before alert B's single attempt. The shared fake LLM
    queue below is consumed in exactly that order: A's three two-call attempts (each an
    unrecoverable `VerdictValidationError` — an empty `"{}"` reply fails validation, and the one
    PRD §6.5 retry, itself `"{}"`, fails again), then B's one one-call attempt.
    """
    alert_a = await seed_alert(db_session, "alert4", session_id="poison-a")
    alert_b = await seed_alert(db_session, "alert4", session_id="poison-b")
    await db_session.commit()

    await enqueue_triage(arq_redis, alert_a)
    await enqueue_triage(arq_redis, alert_b)

    fake = FakeLLMClient(["{}", "{}", "{}", "{}", "{}", "{}", VALID4])
    worker = _worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_retry_settings(base_s=0.0),
        llm=fake,
        max_jobs=1,
    )

    await worker.main()
    await worker.close()

    async with db_session_factory() as fresh:
        row_a = await fresh.get(AlertRow, alert_a)
        row_b = await fresh.get(AlertRow, alert_b)
        assert row_a is not None
        assert row_b is not None
        assert row_a.status == "failed"
        assert row_b.status == "triaged"
        verdict_count_a = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_a)
        )
        verdict_count_b = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_b)
        )
    assert verdict_count_a == 0
    assert verdict_count_b == 1

    assert worker.jobs_retried == 2
    assert worker.jobs_complete == 2
    assert worker.jobs_failed == 0
    assert await arq_redis.zcard(TRIAGE_QUEUE_NAME) == 0
    assert len(fake.calls) == 7


async def test_unexpected_exception_retries_then_fails_with_the_class_logged(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="unexpected-family")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    fake = FakeLLMClient([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
    worker = _worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_retry_settings(),
        llm=fake,
    )

    with caplog.at_level(logging.WARNING):
        await worker.main()
    await worker.close()

    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "failed"

    assert worker.jobs_retried == 2
    assert worker.jobs_complete == 1
    assert worker.jobs_failed == 0

    terminal = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "reason=RuntimeError" in r.getMessage()
    ]
    assert len(terminal) == 1
    # Unexpected (non-SentinelBriefError) families keep their traceback (message text is not the
    # contract either way — no "boom" assertion, per the brief).
    assert terminal[0].exc_info is not None


async def test_missing_alert_is_not_retried(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alert_id = uuid.uuid4()
    await enqueue_triage(arq_redis, alert_id)

    worker = _worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_retry_settings(),
        llm=FakeLLMClient([]),
    )

    with caplog.at_level(logging.WARNING):
        await worker.main()
    await worker.close()

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    assert await job.result() == "missing"
    assert worker.jobs_complete == 1
    assert worker.jobs_retried == 0


async def test_failed_terminal_write_propagates_and_arq_records_the_failure(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="terminal-write-fails")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    # Calls 1-3 (the three attempts) get a real session; call 4 (the terminal `failed` write)
    # gets a session bound to an engine that can never connect (m5 task-02 brief, resolution 4).
    dead_engine = make_engine("postgresql://sentinel:sentinel@127.0.0.1:1/nope")
    dead_factory = make_session_factory(dead_engine)
    calls = 0

    def factory_stub() -> AsyncSession:
        nonlocal calls
        calls += 1
        if calls <= 3:
            return db_session_factory()
        return dead_factory()

    fake = FakeLLMClient([LLMCallError("x"), LLMCallError("x"), LLMCallError("x")])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")
    worker = Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        max_tries=3,
        ctx={"pipeline": pipeline, "session_factory": factory_stub, "settings": _retry_settings()},
    )

    try:
        await worker.main()  # returns normally: ARQ swallows the job-level failure itself
        await worker.close()

        assert worker.jobs_failed == 1
        assert worker.jobs_complete == 0

        async with db_session_factory() as fresh:
            row = await fresh.get(AlertRow, alert_id)
            assert row is not None
            assert row.status == "pending"  # the one documented residual
    finally:
        await dead_engine.dispose()

    assert calls == 4


def test_worker_settings_max_tries_is_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """`WorkerSettings.max_tries` mirrors `Settings.triage_job_max_tries` (m5 task-02 brief,
    resolution 5) — mirrors `tests/test_worker_main.py`'s own reset-and-reimport pattern."""
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "LLM_API_KEY",
        "CHEAP_MODEL",
        "MODEL_PRICES_JSON",
        "TRIAGE_JOB_MAX_TRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    monkeypatch.setenv("TRIAGE_JOB_MAX_TRIES", "4")
    sys.modules.pop("worker.main", None)

    try:
        module = importlib.import_module("worker.main")
        assert module.WorkerSettings.max_tries == 4
    finally:
        sys.modules.pop("worker.main", None)


async def test_successful_job_publishes_verdict_created_once(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="publish-ok")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    pubsub = arq_redis.pubsub()
    await pubsub.subscribe(VERDICT_CREATED_CHANNEL)
    try:
        worker = _worker(
            redis_url=redis_url,
            session_factory=db_session_factory,
            settings=_retry_settings(),
            llm=FakeLLMClient([VALID4]),
        )
        await worker.main()
        await worker.close()

        messages = await _drain_pubsub(pubsub)
        assert len(messages) == 1

        async with db_session_factory() as fresh:
            verdict_row = (
                await fresh.execute(select(VerdictRow).where(VerdictRow.alert_id == alert_id))
            ).scalar_one()

        payload = json.loads(messages[0])
        assert payload == {
            "alert_id": str(alert_id),
            "verdict_id": str(verdict_row.id),
            "severity": 4,
            "category": "successful_intrusion",
            "escalate": True,
            "summary": reasoning_excerpt(verdict_row.reasoning),
        }

        # A `failed` job (poison, same subscription) publishes nothing: still one message total.
        alert_id_2 = await seed_alert(db_session, "alert4", session_id="publish-fail")
        await db_session.commit()
        await enqueue_triage(arq_redis, alert_id_2)

        worker2 = _worker(
            redis_url=redis_url,
            session_factory=db_session_factory,
            settings=_retry_settings(),
            llm=FakeLLMClient([LLMCallError("x"), LLMCallError("x"), LLMCallError("x")]),
        )
        await worker2.main()
        await worker2.close()

        assert await _drain_pubsub(pubsub) == []
    finally:
        await pubsub.aclose()


class _RaisingRedis:
    """A fake Redis whose `publish` always raises `redis.exceptions.ConnectionError` — the direct
    `triage_alert_job` call's own `ctx["redis"]` (m5 task-02 brief, resolution 7); duplicated from
    `tests/test_publish.py` per the repo's own convention (test files never import from each
    other)."""

    async def publish(self, channel: str, message: str) -> int:
        raise redis.exceptions.ConnectionError("down")


async def test_publish_failure_does_not_fail_the_job(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`ctx["redis"]` cannot be swapped under a real `Worker` (ARQ installs it before the first
    job), so this test calls `triage_alert_job` directly — the one documented exception to "drive
    through a real Worker" in this file."""
    alert_id = await seed_alert(db_session, "alert4", session_id="publish-raises-direct")
    await db_session.commit()

    pipeline = TriagePipeline(
        llm=FakeLLMClient([VALID4]), model="fake-model", prompt_version="triage-v1"
    )
    ctx: dict[str, Any] = {
        "pipeline": pipeline,
        "session_factory": db_session_factory,
        "settings": _retry_settings(),
        "redis": _RaisingRedis(),
        "job_id": "triage:test",
        "job_try": 1,
    }

    with caplog.at_level(logging.WARNING):
        result = await triage_alert_job(ctx, str(alert_id))

    assert result == "triaged"

    async with db_session_factory() as fresh:
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 1

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("exc=ConnectionError" in r.getMessage() for r in warnings)
