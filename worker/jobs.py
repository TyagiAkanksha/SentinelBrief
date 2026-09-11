"""`triage_alert_job` — the ARQ job function ARQ's `Worker` dispatches `triage_alert` to
(PRD §6.2, §8; m5 task-01/task-02).

One attempt per ARQ try, through `TriagePipeline.triage_attempt` (which raises on failure after
rolling its own writes back). On any `Exception` the pure policy `worker/retry.py::decide_retry`
says either retry (`raise arq.worker.Retry(defer=...)`, exponential backoff) or, on the last
allowed try, fail: the alert is marked `failed` in its own fresh transaction and the job returns
`"failed"` normally so ARQ keeps draining the queue — a poison alert can never wedge it (the third
documented carve-out to CONVENTIONS.md §4's typed-exception rule: this boundary catches
`Exception`, never `BaseException`, so `asyncio.CancelledError` still propagates). A missing alert
(`NotFoundError`) is never retried. A successful commit publishes one `verdict.created` message
(best-effort; a publish failure never fails the job). A `"skipped"` result (m5 task-04, PRD §6.1
idempotency) means the alert was already triaged/failed when the attempt looked — a re-run after a
crash that had already committed, or an overlapping run — and publishes nothing; M8's retriage
route must flip the status back to `pending` and commit before enqueueing under a fresh job id, or
its own re-run is skipped by this same design.

Every job log line carries ids, counters, `reason=` and `chain=` (exception class names) only —
NEVER `exc_info`/a traceback and NEVER the exception's own message text. Either could render
attacker-derived data (a `pydantic.ValidationError` over `alerts.raw`, or SQLAlchemy's
`[parameters: …]`) straight into the log (PRD §10.6; ruling R9/I1-a).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from arq.worker import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.errors import NotFoundError, SentinelBriefError
from core.services.alerts import set_alert_status
from worker.publish import publish_verdict_created
from worker.retry import decide_retry
from worker.triage import TriagePipeline

logger = logging.getLogger(__name__)

JobResult = Literal["triaged", "failed", "skipped", "missing"]
"""`"skipped"` is minted by task-04 (FOR UPDATE skip on a concurrently-claimed alert); declared
now so the type alias never changes shape underneath callers."""


def _exc_chain(exc: BaseException) -> str:
    """Exception class names along `__cause__`/`__context__` (at most 5), e.g.
    "RuntimeError<-ValueError" — never a message or a traceback: either may embed attacker-derived
    text (PRD §10.6)."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(names) < 5:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return "<-".join(names)


async def triage_alert_job(ctx: Mapping[str, Any], alert_id: str) -> JobResult:
    """Triage `alert_id` through the wired pipeline, retrying on failure with backoff.

    Args:
        ctx: The ARQ job context; must carry `"pipeline"` (`worker.triage.TriagePipeline`),
            `"session_factory"` (an `async_sessionmaker`), and `"settings"` (`core.config
            .Settings`) — all installed by `worker/main.py::WorkerSettings`/`startup`. A missing
            key is a wiring bug and must fail loudly (`KeyError`), not be silently defaulted.
        alert_id: The alert id to triage, as a string (ARQ job arguments must be JSON-encodable).

    Returns:
        `"triaged"` on success; `"failed"` on the last allowed try; `"missing"` when `alert_id`
        does not exist; `"skipped"` when another attempt already triaged/failed the alert (m5
        task-04).

    Raises:
        ValueError: `alert_id` is not a valid UUID (a bug in our own `enqueue_triage`, never real
            input — never retried).
        arq.worker.Retry: The attempt failed and another try remains.
    """
    alert_uuid = uuid.UUID(alert_id)
    pipeline: TriagePipeline = ctx["pipeline"]
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings: Settings = ctx["settings"]
    job_id = ctx.get("job_id")
    job_try = int(ctx.get("job_try") or 1)

    try:
        async with factory() as session:
            attempt = await pipeline.triage_attempt(session, alert_uuid)
    except NotFoundError:
        logger.warning("triage job: alert not found alert_id=%s job_id=%s", alert_id, job_id)
        return "missing"
    except Exception as exc:  # the documented third CONVENTIONS.md §4 carve-out (m5 task-02)
        return await _handle_attempt_failure(
            exc,
            alert_id=alert_id,
            alert_uuid=alert_uuid,
            job_id=job_id,
            job_try=job_try,
            settings=settings,
            factory=factory,
        )

    if (
        attempt.status == "triaged"
        and attempt.verdict_id is not None
        and attempt.outcome is not None
    ):
        await publish_verdict_created(
            ctx["redis"],
            alert_id=alert_uuid,
            verdict_id=attempt.verdict_id,
            verdict=attempt.outcome.verdict,
        )
    return attempt.status


async def _handle_attempt_failure(
    exc: Exception,
    *,
    alert_id: str,
    alert_uuid: uuid.UUID,
    job_id: str | None,
    job_try: int,
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
) -> JobResult:
    """`triage_alert_job`'s exception-handling body (the M7 lift): decide retry-vs-fail for `exc`
    and act on it.

    Args:
        exc: The exception `triage_attempt` raised.
        alert_id: The alert id, as the string `triage_alert_job` was called with (for logging).
        alert_uuid: The same id, parsed, for `mark_alert_failed`.
        job_id: The ARQ job id (for logging).
        job_try: The current ARQ try number.
        settings: The retry policy's config surface.
        factory: The session factory `mark_alert_failed` opens its fresh session through.

    Returns:
        `"failed"` once the alert has been marked `failed` in its own transaction (the last
        allowed try).

    Raises:
        arq.worker.Retry: The attempt failed and another try remains.
    """
    decision = decide_retry(
        exc,
        job_try=job_try,
        max_tries=settings.triage_job_max_tries,
        base_s=settings.triage_job_backoff_base_s,
        max_s=settings.triage_job_backoff_max_s,
    )
    if decision.action == "retry":
        logger.warning(
            "triage job retry alert_id=%s job_id=%s try=%d/%d reason=%s defer_s=%.1f",
            alert_id,
            job_id,
            job_try,
            settings.triage_job_max_tries,
            decision.reason,
            decision.defer_s,
        )
        raise Retry(defer=decision.defer_s) from exc
    log_call = logger.warning if isinstance(exc, SentinelBriefError) else logger.error
    log_call(
        "triage job failed alert_id=%s job_id=%s try=%d/%d reason=%s chain=%s",
        alert_id,
        job_id,
        job_try,
        settings.triage_job_max_tries,
        decision.reason,
        _exc_chain(exc),
    )
    await mark_alert_failed(factory, alert_uuid)
    return "failed"


async def mark_alert_failed(factory: async_sessionmaker[AsyncSession], alert_id: uuid.UUID) -> None:
    """Mark `alert_id` `failed` in its own fresh transaction (the job's terminal write).

    Args:
        factory: The session factory to open a fresh session through.
        alert_id: The alert to mark `failed`.

    Raises:
        Exception: Whatever the DB raises propagates unchanged — if this write itself cannot
            reach the database, the job crashes and ARQ records it as failed; the alert rests
            `pending` (the one documented residual, m5 task-02 brief resolution 2).
    """
    async with factory() as session:
        await set_alert_status(session, alert_id, "failed")
        await session.commit()
