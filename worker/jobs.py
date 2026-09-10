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
(best-effort; a publish failure never fails the job).
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
        does not exist.

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
            "triage job failed alert_id=%s job_id=%s try=%d/%d reason=%s",
            alert_id,
            job_id,
            job_try,
            settings.triage_job_max_tries,
            decision.reason,
            exc_info=not isinstance(exc, SentinelBriefError),
        )
        await mark_alert_failed(factory, alert_uuid)
        return "failed"

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
