"""Pins idempotent triage-job execution (m5 task-04, PRD §6.1 idempotency, §6.2 one-transaction
verdict write, §12 M5 "kill the worker mid-job -> job re-runs, no duplicate verdicts").

A job re-run -- direct double invocation, two overlapping attempts, a graceful SIGTERM
mid-LLM-call, a SIGKILL whose lease outlives the process, or a commit ARQ itself never saw
finish -- must produce exactly one verdict row and one recorded token spend for the alert; the
losing/second run reports `"skipped"` before any LLM call (`worker.triage.TriagePipeline
.triage_attempt`'s `get_alert_for_update` row lock, held across the LLM call on purpose -- the
M5 spine's "row lock, not SKIP LOCKED" ruling).

`BlockingFakeLLMClient` (below) is the one LLM double this file adds on top of
`tests.fakes.FakeLLMClient` (CONVENTIONS.md §10: the vendor call is the only thing faked): its
`complete_structured` sets `started` the instant it is entered and awaits `release` before
delegating to the real fake, so a test can observe "the row lock is held and the LLM call is in
flight" from outside. Every test that uses it sets `release` in a `finally` and closes/awaits
every worker/task it started, so a failing assertion can never hang the suite (pytest-timeout's
120 s is the backstop, not the plan).

Every DB-touching test uses the throwaway-schema fixtures; every Redis-touching test uses
`arq_redis`/`redis_url` (the dedicated test Redis, flushed). Ordering assumptions are stated in
each concurrency test's own docstring (M4 plan-defect rule 9); every count is asserted only after
everything has finished.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import Sequence
from typing import Any

from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus
from arq.worker import Worker
from arq.worker import func as arq_func
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.llm import ChatMessage, LLMResult, parse_structured
from core.models import AlertRow, VerdictRow
from core.queue import (
    TRIAGE_JOB_NAME,
    TRIAGE_QUEUE_NAME,
    enqueue_triage,
    redis_settings,
    triage_job_id,
)
from tests.fakes import FakeCall, FakeLLMClient, FakeReply
from tests.helpers import VALID4, seed_alert
from worker.jobs import triage_alert_job
from worker.triage import AttemptResult, TriagePipeline


def _settings(*, base_s: float = 0.0, max_s: float = 0.05, max_tries: int = 3) -> Settings:
    """The retry `Settings` every test in this file threads into `ctx["settings"]`. Duplicated
    from `tests/test_worker_job_retry.py::_retry_settings` per this repo's own convention (test
    files never import from each other) -- none of the tests below exercise backoff timing
    itself, so `base_s=0.0` keeps any incidental retry instant."""
    return Settings(
        triage_job_max_tries=max_tries,
        triage_job_backoff_base_s=base_s,
        triage_job_backoff_max_s=max_s,
    )


def _burst_worker(
    *,
    redis_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    llm: Any,
    handle_signals: bool = True,
    poll_delay: float = 0.01,
) -> Worker:
    """The burst `Worker` pattern from `tests/test_worker_job.py`/`tests/test_worker_job_retry.py`
    (duplicated per this repo's own convention: test files never import from each other), plus
    `handle_signals=False` for the cancel/crash tests below, which drive the worker through
    `async_run()`/`handle_sig()` directly instead of letting ARQ install real OS signal handlers.
    Never carries `"redis"` in `ctx` -- ARQ installs that key itself before the first job."""
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    return Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        handle_signals=handle_signals,
        poll_delay=poll_delay,
        max_tries=3,
        ctx={"pipeline": pipeline, "session_factory": session_factory, "settings": settings},
    )


class _RecordingRedis:
    """A fake Redis that records every `publish` call (the direct `triage_alert_job` call's own
    `ctx["redis"]`, mirroring `tests/test_worker_job_retry.py::_RaisingRedis`'s shape but
    recording instead of raising): the skipped run must publish nothing, so a second
    `triage_alert_job` call must never bump this count."""

    def __init__(self) -> None:
        self.publish_calls = 0

    async def publish(self, channel: str, message: str) -> int:
        self.publish_calls += 1
        return 1


class BlockingFakeLLMClient(FakeLLMClient):
    """An LLM double for the vendor call itself (CONVENTIONS.md §10: mock only external seams)
    that blocks INSIDE the call, AFTER recording it: `complete_structured` records the call (so
    `.calls` reflects an in-flight call the instant `started` fires, not only once it returns),
    sets `started`, and awaits `release` before popping and replaying the next queued response --
    the only way to hold a `triage_attempt`'s row lock open across an LLM call under test control,
    so the overlap/cancel/crash tests below can observe "still locked, still running" from
    outside. Recording and replaying can't both be delegated to one `super()` call the way a
    non-blocking double would (`FakeLLMClient.complete_structured` does both atomically); the
    queue-pop/dispatch below is the minimal duplication needed to split them in time, and it still
    validates a `str` reply through the real `core.llm.parse_structured` path, never hand-rolled.
    """

    def __init__(
        self, responses: Sequence[FakeReply], *, started: asyncio.Event, release: asyncio.Event
    ) -> None:
        super().__init__(responses)
        self._started = started
        self._release = release

    async def complete_structured(
        self, *, messages: Sequence[ChatMessage], response_model: type[BaseModel], model: str
    ) -> LLMResult[Any]:
        self.calls.append(
            FakeCall(messages=list(messages), response_model=response_model, model=model)
        )
        self._started.set()
        await self._release.wait()
        if not self._responses:
            raise AssertionError("FakeLLMClient: no responses left")
        next_response = self._responses.pop(0)
        if isinstance(next_response, str):
            return parse_structured(
                next_response,
                response_model,
                model=model,
                usage=self._usage,
                cost_usd=self._cost_usd,
                latency_ms=self._latency_ms,
            )
        if isinstance(next_response, Exception):
            raise next_response
        raise AssertionError(
            "FakeLLMClient: tool calls were scripted but complete_structured was called"
        )


async def test_running_the_job_twice_writes_one_verdict_and_spends_once(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Direct double invocation, no `Worker`, no queue: "no double token spend recorded" means
    the second call's verdict row (there must be only one) still carries the FIRST run's own
    `input_tokens`, and the fake Redis's `publish` was only ever called once -- the skipped run
    publishes nothing.
    """
    alert_id = await seed_alert(db_session, "alert4", session_id="double-invoke-001")
    await db_session.commit()

    # Two queued replies, not one: today's `triage_attempt` has no status check at all, so an
    # under-stocked fake would make the second call raise `AssertionError("no responses left")`
    # (surfacing as a retried-then-failed job) instead of the load-bearing RED this test pins --
    # two full successful runs, and a SECOND verdict row where only one may ever exist.
    llm = FakeLLMClient([VALID4, VALID4])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    recording_redis = _RecordingRedis()
    ctx: dict[str, Any] = {
        "pipeline": pipeline,
        "session_factory": db_session_factory,
        "settings": _settings(),
        "redis": recording_redis,
        "job_id": triage_job_id(alert_id),
        "job_try": 1,
    }

    first = await triage_alert_job(ctx, str(alert_id))
    second = await triage_alert_job(ctx, str(alert_id))

    assert first == "triaged"
    assert second == "skipped"
    assert len(llm.calls) == 1

    async with db_session_factory() as fresh:
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 1

    async with db_session_factory() as fresh:
        verdict = (
            (await fresh.execute(select(VerdictRow).where(VerdictRow.alert_id == alert_id)))
            .scalars()
            .first()
        )
    assert verdict is not None
    assert verdict.input_tokens == 100

    assert recording_redis.publish_calls == 1


async def test_overlapping_attempts_serialise_on_the_row_lock_and_the_loser_skips(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two `TriagePipeline`s share one `BlockingFakeLLMClient` and race `triage_attempt` over the
    SAME alert row, each through its own session. Ordering assumption stated (rule 9): `db_engine`
    has a pool of >= 2 connections (SQLAlchemy's default is 5), so session A's held lock and
    session B's blocked `SELECT ... FOR UPDATE` can be open at once without B's own connection
    checkout being what actually blocks it -- Postgres's row lock must be the thing under test,
    not pool exhaustion. `release` is set in `finally`; both tasks are cancelled-and-awaited there
    too, so a failing assertion above can never hang the suite.
    """
    alert_id = await seed_alert(db_session, "alert4", session_id="overlap-001")
    await db_session.commit()

    started = asyncio.Event()
    release = asyncio.Event()
    llm = BlockingFakeLLMClient([VALID4], started=started, release=release)
    pipeline_a = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    pipeline_b = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")

    async with db_session_factory() as session_a, db_session_factory() as session_b:
        task_a: asyncio.Task[AttemptResult] = asyncio.create_task(
            pipeline_a.triage_attempt(session_a, alert_id)
        )
        task_b: asyncio.Task[AttemptResult] | None = None
        try:
            await asyncio.wait_for(started.wait(), timeout=2.0)

            task_b = asyncio.create_task(pipeline_b.triage_attempt(session_b, alert_id))
            await asyncio.sleep(0.3)

            assert len(llm.calls) == 1  # B is blocked in the DB, before it ever reaches the LLM
            assert not task_b.done()

            release.set()
            results = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=5.0)
        finally:
            release.set()
            for task in (task_a, task_b):
                if task is not None and not task.done():
                    task.cancel()
            for task in (task_a, task_b):
                if task is not None:
                    with contextlib.suppress(BaseException):
                        await task

    assert {r.status for r in results} == {"triaged", "skipped"}
    assert len(llm.calls) == 1

    async with db_session_factory() as fresh:
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 1


async def test_cancelled_run_is_re_run_by_the_next_worker_with_one_verdict(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    """ARQ's own graceful SIGTERM path (`Worker.handle_sig`, `handle_signals=False` here so the
    test drives it directly instead of a real OS signal): cancels every running job task and the
    poll loop; `run_job`'s cancel branch logs "cancelled, will be run again", deletes the
    in-progress key, and leaves the job queued so the very next worker picks it straight back up.
    Assumption stated (rule 9): the blocking fake never returns its reply, so no `TriageOutcome`
    usage is ever persisted for this attempt -- the connection drop (here, the row-lock
    transaction's own rollback on cancellation) leaves no trace at all. `release` is set in
    `finally` so a failing assertion can never hang the suite.
    """
    alert_id = await seed_alert(db_session, "alert4", session_id="cancel-rerun-001")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    started = asyncio.Event()
    release = asyncio.Event()
    llm = BlockingFakeLLMClient([VALID4], started=started, release=release)
    worker1 = _burst_worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_settings(),
        llm=llm,
        handle_signals=False,
    )

    task = asyncio.create_task(worker1.async_run())
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        worker1.handle_sig(signal.SIGTERM)
        await asyncio.gather(task, return_exceptions=True)
    finally:
        release.set()
        await worker1.close()

    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "pending"
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 0
    assert await arq_redis.zcard(TRIAGE_QUEUE_NAME) == 1  # still queued, not lost
    assert await arq_redis.exists("arq:in-progress:" + triage_job_id(alert_id)) == 0
    assert worker1.jobs_retried == 1

    fake2 = FakeLLMClient([VALID4])
    worker2 = _burst_worker(
        redis_url=redis_url, session_factory=db_session_factory, settings=_settings(), llm=fake2
    )
    await worker2.main()
    await worker2.close()

    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "triaged"
        verdict = (
            await fresh.execute(select(VerdictRow).where(VerdictRow.alert_id == alert_id))
        ).scalar_one()
    assert verdict.input_tokens == 100
    assert worker2.jobs_complete == 1


async def test_crashed_run_waits_for_the_lease_then_re_runs_once(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    """Emulates a SIGKILL rather than the graceful SIGTERM above: worker1's own cancel-and-close
    already deleted the in-progress key (ARQ's graceful path always does), so it is re-created by
    hand exactly as a SIGKILL would have left it -- a real crash never runs ARQ's own cleanup at
    all. Assumption stated (rule 9): the lease TTL (1.5 s) is the only thing worker2 waits on --
    `poll_delay=0.05` keeps burst mode's own "still queued, but already running elsewhere" polling
    loop from adding meaningfully to that wait.
    """
    alert_id = await seed_alert(db_session, "alert4", session_id="crash-lease-001")
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    started = asyncio.Event()
    release = asyncio.Event()
    llm = BlockingFakeLLMClient([VALID4], started=started, release=release)
    worker1 = _burst_worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_settings(),
        llm=llm,
        handle_signals=False,
    )

    task = asyncio.create_task(worker1.async_run())
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        worker1.handle_sig(signal.SIGTERM)
        await asyncio.gather(task, return_exceptions=True)
    finally:
        release.set()
        await worker1.close()

    # Re-create the lease as a SIGKILL would have left it (worker1's own graceful shutdown just
    # deleted it above).
    await arq_redis.psetex("arq:in-progress:" + triage_job_id(alert_id), 1500, b"1")
    assert (
        await Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME).status()
        is JobStatus.in_progress
    )

    fake2 = FakeLLMClient([VALID4])
    worker2 = _burst_worker(
        redis_url=redis_url,
        session_factory=db_session_factory,
        settings=_settings(),
        llm=fake2,
        poll_delay=0.05,
    )
    t0 = time.perf_counter()
    await worker2.main()
    await worker2.close()
    elapsed_s = time.perf_counter() - t0

    assert elapsed_s >= 1.4
    assert worker2.jobs_complete == 1
    async with db_session_factory() as fresh:
        row = await fresh.get(AlertRow, alert_id)
        assert row is not None
        assert row.status == "triaged"
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 1
    assert len(fake2.calls) == 1


async def test_run_after_a_committed_but_unfinished_job_skips(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    """Emulates a worker whose process died AFTER `triage_attempt`'s own commit but BEFORE ARQ's
    `finish_job` ever ran -- there is no ARQ `Worker` at all for the crashed half: the direct
    `triage_alert_job` call plays the crashed run, and the one `FakeLLMClient` is shared with the
    fresh pipeline the re-run `Worker` gets, so `len(llm.calls) == 1` proves the second run never
    reached the LLM.
    """
    alert_id = await seed_alert(db_session, "alert4", session_id="committed-unfinished-001")
    await db_session.commit()

    llm = FakeLLMClient([VALID4])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    ctx: dict[str, Any] = {
        "pipeline": pipeline,
        "session_factory": db_session_factory,
        "settings": _settings(),
        "redis": _RecordingRedis(),
        "job_id": triage_job_id(alert_id),
        "job_try": 1,
    }
    result = await triage_alert_job(ctx, str(alert_id))
    assert result == "triaged"

    assert await enqueue_triage(arq_redis, alert_id) is True  # no kept result exists yet

    worker = Worker(
        functions=[arq_func(triage_alert_job, name=TRIAGE_JOB_NAME)],
        queue_name=TRIAGE_QUEUE_NAME,
        redis_settings=redis_settings(redis_url),
        burst=True,
        poll_delay=0.01,
        max_tries=3,
        ctx={"pipeline": pipeline, "session_factory": db_session_factory, "settings": _settings()},
    )
    await worker.main()
    await worker.close()

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    assert await job.result() == "skipped"
    assert worker.jobs_complete == 1

    async with db_session_factory() as fresh:
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 1
    assert len(llm.calls) == 1


async def test_failed_alert_is_skipped_not_re_triaged(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
    redis_url: str,
) -> None:
    """M8's retriage route must `set_alert_status(..., "pending")` and commit BEFORE enqueueing
    under a fresh job id (Interfaces block note) -- otherwise a `failed` alert's row lock still
    shows a non-`pending` status here, by design, and this skip fires instead: no new verdict is
    ever written, and the LLM is never called.
    """
    alert_id = await seed_alert(
        db_session, "alert4", status="failed", session_id="failed-not-retried-001"
    )
    await db_session.commit()
    await enqueue_triage(arq_redis, alert_id)

    fake = FakeLLMClient([])
    worker = _burst_worker(
        redis_url=redis_url, session_factory=db_session_factory, settings=_settings(), llm=fake
    )
    await worker.main()
    await worker.close()

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    assert await job.result() == "skipped"
    assert worker.jobs_complete == 1

    async with db_session_factory() as fresh:
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert verdict_count == 0
    assert len(fake.calls) == 0
