"""`create_app()` — the DB-less constructible FastAPI app (CONVENTIONS.md §5).

Everything request-scoped lives on `app.state`, read back through `api/deps.py`. `create_app()`
must succeed with no database and no env vars wired: that is what makes the OpenAPI baseline
export (§8) and the DB-less test suite possible.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.deps import TriageFn
from api.errors import register_error_handlers
from api.routes.health import router as health_router
from core.config import Settings

# All non-health routes live under this prefix (CONVENTIONS.md §5); the alerts router lands here
# in task-03.
API_V1_PREFIX = "/api/v1"


def create_app(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    triage: TriageFn | None = None,
) -> FastAPI:
    """Build the FastAPI app with the request-scoped seams stored on `app.state`.

    Args:
        session_factory: The DB session factory, or `None` to leave the DB unwired (DB-less
            tests, the OpenAPI export); `api/deps.py::get_session` raises `RuntimeError` at
            request time when unwired.
        settings: The app's `Settings`, or `None` to leave `get_settings` defaulting to a
            zero-env `Settings()`.
        triage: The triage callable, or `None` to leave `get_triage` raising `RuntimeError` at
            request time when unwired.

    Returns:
        A configured `FastAPI` instance. Never touches the network or the filesystem.
    """
    app = FastAPI()
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.triage = triage

    effective_settings = settings if settings is not None else Settings()
    app.add_middleware(CORSMiddleware, allow_origins=effective_settings.cors_origin_list)

    register_error_handlers(app)

    app.include_router(health_router)

    return app
