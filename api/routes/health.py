"""`GET /healthz` — DB + Redis liveness (PRD §8; CONVENTIONS.md §4's one documented try/except
carve-out, extended at m5 task-05 with a Redis branch).

A liveness probe must never look like an application crash: DB/Redis errors are caught here and
answered as `503`, unlike every other route, which leaves error mapping to the handlers
registered by `api/errors.py::register_error_handlers`. Both probes always run and are always
reported, independently of one another: `db` (`SELECT 1` through the wired session factory) and
`redis` (`PING` through `app.state.redis`) — this is what makes the compose `HEALTHCHECK` turn the
api unhealthy the moment either seam goes away.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.schemas.health import HealthResponse

router = APIRouter()


async def _db_status(request: Request) -> Literal["ok", "error", "unconfigured"]:
    """Probe the database: `SELECT 1` through `app.state.session_factory`."""
    factory = request.app.state.session_factory
    if factory is None:
        return "unconfigured"
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        return "error"
    return "ok"


async def _redis_status(request: Request) -> Literal["ok", "error", "unconfigured"]:
    """Probe Redis: `PING` through `app.state.redis`."""
    redis = request.app.state.redis
    if redis is None:
        return "unconfigured"
    try:
        await redis.ping()
    except (RedisError, OSError):
        return "error"
    return "ok"


@router.get(
    "/healthz",
    operation_id="healthz",
    response_model=HealthResponse,
    responses={
        503: {
            "model": HealthResponse,
            "description": "Degraded: database and/or Redis unconfigured or unreachable.",
        }
    },
)
async def healthz(request: Request) -> JSONResponse:
    """Report DB + Redis liveness.
    \f
    Args:
        request: The current request, used to reach `app.state.session_factory`/`app.state.redis`.

    Returns:
        `200 {"status": "ok", "db": "ok", "redis": "ok"}` when both answer; `503
        {"status": "degraded", ...}` naming the failing side(s) as `"error"` (unreachable) or
        `"unconfigured"` (the seam was never wired) otherwise. Both probes always run.
    """
    db = await _db_status(request)
    redis = await _redis_status(request)
    status = "ok" if db == "ok" and redis == "ok" else "degraded"
    body = {"status": status, "db": db, "redis": redis}
    return JSONResponse(status_code=200 if status == "ok" else 503, content=body)
