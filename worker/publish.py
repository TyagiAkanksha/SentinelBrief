"""`verdict.created` Redis pub/sub publish — best-effort, after a triage commit (PRD §8 `/stream`
row, §6.2; m5 task-02 brief, Interfaces block).

`publish_verdict_created` NEVER raises: `worker/jobs.py` calls it only after `triage_attempt`'s own
commit has already made the verdict durable, so a dead Redis here is a WARNING, never a failed job.
`summary` is model text derived from attacker-influenced session data (PRD §10.6): the M8 SSE
consumer renders it as text, never as instructions.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from core.queue import VERDICT_CREATED_CHANNEL
from core.schemas.alerts_read import reasoning_excerpt
from core.schemas.verdict import Verdict

logger = logging.getLogger(__name__)


def verdict_created_payload(
    *, alert_id: uuid.UUID, verdict_id: uuid.UUID, verdict: Verdict
) -> dict[str, Any]:
    """Build the `verdict.created` message body (PRD §8: "(id, severity, category, summary line)").

    Args:
        alert_id: The alert the verdict belongs to.
        verdict_id: The new verdict row's id.
        verdict: The verdict to summarize.

    Returns:
        A JSON-serializable mapping with exactly the keys `alert_id`, `verdict_id`, `severity`,
        `category`, `escalate`, `summary`.
    """
    return {
        "alert_id": str(alert_id),
        "verdict_id": str(verdict_id),
        "severity": verdict.severity,
        "category": verdict.category,
        "escalate": verdict.escalate,
        "summary": reasoning_excerpt(verdict.reasoning),
    }


async def publish_verdict_created(
    redis: Redis, *, alert_id: uuid.UUID, verdict_id: uuid.UUID, verdict: Verdict
) -> bool:
    """Publish `verdict_created_payload(...)` on `VERDICT_CREATED_CHANNEL`; never raises.

    Args:
        redis: The Redis client to publish through (`ctx["redis"]`, installed by ARQ's `Worker`).
        alert_id: The alert the verdict belongs to.
        verdict_id: The new verdict row's id.
        verdict: The verdict to summarize.

    Returns:
        `True` when the publish call succeeded; `False` on a `RedisError`/`OSError` (logged as a
        WARNING naming both ids and the exception class only — never the exception's own message,
        which could embed connection details).
    """
    try:
        await redis.publish(
            VERDICT_CREATED_CHANNEL,
            json.dumps(
                verdict_created_payload(alert_id=alert_id, verdict_id=verdict_id, verdict=verdict)
            ),
        )
    except (RedisError, OSError) as exc:
        logger.warning(
            "verdict.created publish failed alert_id=%s verdict_id=%s exc=%s",
            alert_id,
            verdict_id,
            type(exc).__name__,
        )
        return False
    return True
