"""`triage_alert_job` — the ARQ job function ARQ's `Worker` dispatches `triage_alert` to
(PRD §6.1 step 3, §6.2; m5 task-01).

Loads the alert through the pipeline's own `triage_alert` (unchanged semantics from M2: one
attempt, a validation/LLM failure marks the alert `failed`; task-02 adds retries) and returns a
`JobResult`. A malformed alert id (a bug in our own `enqueue_triage`, never real input) raises
`ValueError` before ever touching `ctx`. An alert id that no longer exists answers `"missing"`
without a retry, logging only the id (never the payload — `.claude/rules/worker.md`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any, Literal, cast

from core.errors import NotFoundError
from worker.triage import TriagePipeline

logger = logging.getLogger(__name__)

JobResult = Literal["triaged", "failed", "skipped", "missing"]
"""`"skipped"` is minted by task-04 (FOR UPDATE skip on a concurrently-claimed alert); declared
now so the type alias never changes shape underneath callers."""


async def triage_alert_job(ctx: Mapping[str, Any], alert_id: str) -> JobResult:
    """Triage `alert_id` through the wired pipeline, using the wired session factory.

    Args:
        ctx: The ARQ job context; must carry `"pipeline"` (`worker.triage.TriagePipeline`) and
            `"session_factory"` (an `async_sessionmaker`), both installed by `worker/main.py
            ::startup`.
        alert_id: The alert id to triage, as a string (ARQ job arguments must be JSON-encodable).

    Returns:
        `"triaged"` or `"failed"` (`TriagePipeline.triage_alert`'s own outcomes, this task); or
        `"missing"` when `alert_id` does not exist.

    Raises:
        ValueError: `alert_id` is not a valid UUID.
    """
    alert_uuid = uuid.UUID(alert_id)
    pipeline: TriagePipeline = ctx["pipeline"]
    async with ctx["session_factory"]() as session:
        try:
            # `triage_alert`'s own return type is `AlertStatus` (it also covers the still-`pending`
            # case that never applies to an already-`pending` row this job just claimed); this
            # task's semantics only ever produce "triaged"/"failed" here (task-02 stays within
            # `JobResult`'s shape too).
            return cast(JobResult, await pipeline.triage_alert(session, alert_uuid))
        except NotFoundError:
            logger.warning(
                "triage job: alert not found alert_id=%s job_id=%s", alert_id, ctx.get("job_id")
            )
            return "missing"
