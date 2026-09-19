"""`POST /api/v1/admin/retriage/{alert_id}` — admin-token-gated, globally-capped retriage
(PRD §8, §10; m8b task-04; the m5 task-04 deferral "the retriage flip needs `SET LOCAL
lock_timeout` + 409").

The ONLY non-nightly path that causes an LLM call (PRD §10.1) — and it never runs from an
unauthenticated request: every route here is gated by `require_admin_token`. The global daily cap
(one Redis counter keyed on the UTC date, shared across ALL admins) is checked BEFORE any DB work,
so a caller past the cap never even reaches the alert lookup. `api/` still never imports `worker`
or `core.llm`: this route only enqueues via the `EnqueueFn` seam (`api.deps.get_enqueue`), exactly
like `api/routes/alerts.py::ingest_alert`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from redis.exceptions import RedisError

from api.deps import EnqueueFn, SessionDep, get_enqueue, get_settings, require_admin_token
from core.config import Settings
from core.errors import ConflictError, QueueUnavailableError, RateLimitedError
from core.schemas.admin import RetriageResponse
from core.schemas.errors import ErrorEnvelope
from core.services.alerts import flip_alert_to_pending, get_alert_for_retriage_update

router = APIRouter()

_RETRIAGE_COUNTER_TTL_S = 172_800
"""~2 days — comfortably outlives one UTC day so the counter is never read after its key has
already expired, while still not accumulating stale per-day keys forever."""


async def _check_daily_retriage_cap(request: Request, settings: Settings) -> None:
    """Raise before any DB work when today's global retriage count exceeds `RETRIAGE_PER_DAY`.

    One Redis counter (`f"retriage:{utc_date}"`), incremented and given a ~2 day expiry in one
    pipelined round trip — shared across ALL admins, never per-IP/per-token (PRD §8).

    Args:
        request: The current request; used to reach `app.state.redis`.
        settings: The app's `Settings`, for `retriage_per_day`.

    Raises:
        QueueUnavailableError: Redis is unwired or unreachable (a `503`; unlike the public GET
            rate limiter, retriage never fails open — an uncounted retriage could blow the daily
            LLM budget).
        RateLimitedError: The cap is exceeded (a `429`).
    """
    redis = request.app.state.redis
    if redis is None:
        raise QueueUnavailableError("retriage counter unavailable")
    key = f"retriage:{datetime.now(UTC).date().isoformat()}"
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, _RETRIAGE_COUNTER_TTL_S)
        count, _ = await pipe.execute()
    except (RedisError, OSError) as exc:
        raise QueueUnavailableError("retriage counter unavailable") from exc
    if count > settings.retriage_per_day:
        raise RateLimitedError("daily retriage cap exceeded")


@router.post(
    "/admin/retriage/{alert_id}",
    operation_id="retriage_alert",
    response_model=RetriageResponse,
    status_code=202,
    dependencies=[Depends(require_admin_token)],
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid admin bearer token."},
        404: {"model": ErrorEnvelope, "description": "No alert with this id."},
        409: {
            "model": ErrorEnvelope,
            "description": "The alert is not triaged/failed, or its row is locked by another "
            "request past RETRIAGE_LOCK_TIMEOUT_MS.",
        },
        422: {"model": ErrorEnvelope, "description": "Invalid alert_id."},
        429: {
            "model": ErrorEnvelope,
            "description": "The global daily retriage cap (RETRIAGE_PER_DAY) is reached.",
        },
        500: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope, "description": "The retriage counter/queue is unavailable."},
    },
)
async def retriage_alert(
    alert_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
    enqueue: EnqueueFn = Depends(get_enqueue),
) -> RetriageResponse:
    """Flip a `triaged`/`failed` alert back to `pending` and re-enqueue it for triage.
    \f
    Args:
        alert_id: The alert to retriage.
        request: The current request; used to reach `app.state.redis` for the daily cap.
        session: The request-scoped session (`SessionDep`).
        settings: The app's `Settings`, for `retriage_per_day`/`retriage_lock_timeout_ms`.
        enqueue: The wired queue callable (`api.deps.get_enqueue`), invoked once, AFTER the
            status flip is committed (spine M5-a: the row must be durable before a worker can
            pick the job up).

    Returns:
        `202 {"id", "status": "pending", "retriaged": true}`.

    Raises:
        NotFoundError: No alert with `alert_id` exists (mapped to 404).
        ConflictError: The alert's current status is neither `triaged` nor `failed`, or its row
            is locked by another request past `retriage_lock_timeout_ms` (mapped to 409).
    """
    await _check_daily_retriage_cap(request, settings)

    row = await get_alert_for_retriage_update(
        session, alert_id, lock_timeout_ms=settings.retriage_lock_timeout_ms
    )
    if row.status not in ("triaged", "failed"):
        raise ConflictError(f"alert {alert_id} is not eligible for retriage (status={row.status})")

    await flip_alert_to_pending(session, row)
    # Spine M5-a: commit before enqueueing so a worker can never pick up a job before the row's
    # pending status is durable (mirrors api/routes/alerts.py::ingest_alert).
    await session.commit()
    await enqueue(alert_id)

    return RetriageResponse(id=alert_id, status="pending", retriaged=True)
