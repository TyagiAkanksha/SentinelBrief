"""The ARQ queue seam: job/queue names, the job id, `enqueue_triage`, and the Redis client
factories (PRD §4, §6.1 step 3; m5 task-01).

The ONLY module that knows ARQ's job name, queue name and job-id shape — both `api/` and
`worker/` build their Redis seam from this module (`api` never imports `worker`). `make_redis`
and `redis_settings` are sync, lazy-connect factories usable at module-import time (no event loop
needed): `api/main.py` and `worker/main.py`'s `WorkerSettings` both call them at wiring time.
"""

from __future__ import annotations

import logging
import uuid

from arq.connections import ArqRedis, RedisSettings
from redis.asyncio import ConnectionPool
from redis.exceptions import RedisError

from core.errors import QueueUnavailableError

logger = logging.getLogger(__name__)

TRIAGE_JOB_NAME = "triage_alert"
TRIAGE_QUEUE_NAME = "sentinelbrief:triage"


def triage_job_id(alert_id: uuid.UUID) -> str:
    """The one ARQ job id per alert — a second `enqueue_triage` for the same alert is a no-op.

    Args:
        alert_id: The alert to build a job id for.

    Returns:
        `f"triage:{alert_id}"`.
    """
    return f"triage:{alert_id}"


def make_redis(redis_url: str, *, socket_timeout_s: float) -> ArqRedis:
    """Build an `ArqRedis` client, synchronously and without connecting (lazy connect).

    Usable from module-level wiring (`api/main.py`), which has no running event loop yet. The
    caller owns `aclose()`.

    Args:
        redis_url: The Redis DSN to connect to.
        socket_timeout_s: Both the connect and socket timeout, in seconds — so a dead Redis
            fails fast rather than hanging the caller.

    Returns:
        An `ArqRedis` client bound to the `TRIAGE_QUEUE_NAME` queue by default.
    """
    pool = ConnectionPool.from_url(
        redis_url, socket_timeout=socket_timeout_s, socket_connect_timeout=socket_timeout_s
    )
    return ArqRedis(connection_pool=pool, default_queue_name=TRIAGE_QUEUE_NAME)


def redis_settings(redis_url: str) -> RedisSettings:
    """Build the `RedisSettings` ARQ's `Worker`/`WorkerSettings` connect through.

    Args:
        redis_url: The Redis DSN to parse.

    Returns:
        `RedisSettings.from_dsn(redis_url)`.
    """
    return RedisSettings.from_dsn(redis_url)


async def enqueue_triage(redis: ArqRedis, alert_id: uuid.UUID) -> bool:
    """Enqueue one `triage_alert` job for `alert_id`, idempotent per alert.

    Args:
        redis: The `ArqRedis` client to enqueue through.
        alert_id: The alert to triage.

    Returns:
        `True` when a new job was queued; `False` when a job with this id already exists
        (queued, running, or its result is still kept — ARQ's own idempotency).

    Raises:
        QueueUnavailableError: Redis could not be reached. The message never contains the
            connection URL, a host, or a password — only the fixed string below.
    """
    try:
        job = await redis.enqueue_job(
            TRIAGE_JOB_NAME,
            str(alert_id),
            _job_id=triage_job_id(alert_id),
            _queue_name=TRIAGE_QUEUE_NAME,
        )
    except (RedisError, OSError) as exc:
        raise QueueUnavailableError("triage queue unavailable") from exc
    return job is not None
