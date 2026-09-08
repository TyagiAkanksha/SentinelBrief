"""`GET /healthz` — DB liveness (PRD §8; CONVENTIONS.md §4's one documented try/except carve-out).

A liveness probe must never look like an application crash: DB/connection errors are caught here
and answered as `503`, unlike every other route, which leaves error mapping to the handlers
registered by `api/errors.py::register_error_handlers`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()


@router.get("/healthz", operation_id="healthz")
async def healthz(request: Request) -> JSONResponse:
    """Report DB liveness by running `SELECT 1` through the wired session factory.

    Args:
        request: The current request, used to reach `app.state.session_factory`.

    Returns:
        `200 {"status": "ok", "db": "ok"}` when the database answers;
        `503 {"status": "degraded", "db": "unconfigured"}` when no session factory is wired;
        `503 {"status": "degraded", "db": "error"}` when the database is unreachable.
    """
    factory = request.app.state.session_factory
    if factory is None:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "unconfigured"})

    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "error"})

    return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})
