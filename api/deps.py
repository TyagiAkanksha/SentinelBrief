"""Request-scoped seams read off `app.state` (CONVENTIONS.md §5).

Routes never read `app.state` or `os.environ` directly — they depend on `get_settings`,
`get_session` and `get_triage`, which are the only places that know how those seams are wired.
Nothing here imports `worker` or `core.llm` (PRD §10.1): `TriageFn` is a plain callable type alias
over `AsyncSession`/`uuid.UUID`/`AlertStatus`, never a `worker` type.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from core.models.alerts import AlertStatus

TriageFn = Callable[[AsyncSession, uuid.UUID], Awaitable[AlertStatus]]


def get_settings(request: Request) -> Settings:
    """Return the app's `Settings`, defaulting to a zero-env `Settings()` when none is wired.

    Args:
        request: The current request, used to reach `app.state`.

    Returns:
        The `Settings` instance `create_app()` was built with, or a fresh default one.
    """
    settings: Settings | None = request.app.state.settings
    if settings is None:
        return Settings()
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the wired `session_factory`, owning commit/rollback.

    Args:
        request: The current request, used to reach `app.state.session_factory`.

    Yields:
        An `AsyncSession` for the route to use.

    Raises:
        RuntimeError: When no `session_factory` was wired into `create_app()`.
    """
    factory = request.app.state.session_factory
    if factory is None:
        raise RuntimeError("no session_factory wired")
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Routes take `session: SessionDep`, never a bare `Depends(get_session)`. FastAPI's default
# dependency scope for a yield-dependency is `"request"`, which runs the code after `yield`
# (the commit/rollback here) only after the response has already been sent to the client — a
# failing `commit()` would then be swallowed and the client would see the route handler's `200`
# for a write that never persisted. `scope="function"` runs that exit code **before** the
# response is built, so a failing commit propagates through `register_error_handlers` as the
# generic 500 envelope instead (CONVENTIONS.md §3, §5).
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_triage(request: Request) -> TriageFn:
    """Return the wired triage callable.

    Args:
        request: The current request, used to reach `app.state.triage`.

    Returns:
        The `TriageFn` `create_app()` was built with.

    Raises:
        RuntimeError: When no `triage` was wired into `create_app()`.
    """
    triage: TriageFn | None = request.app.state.triage
    if triage is None:
        raise RuntimeError("no triage wired")
    return triage
