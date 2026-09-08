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

from core.cache import TTLCache
from core.config import Settings
from core.errors import SignatureError
from core.models.alerts import AlertStatus
from core.signing import SIGNATURE_HEADER, verify_signature

# A `TriageFn` persists the status it returns (verdict + status in one transaction, PRD §6.2);
# the route that calls it never writes `alerts.status` itself.
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


def get_cache(request: Request) -> TTLCache:
    """Return the app's `TTLCache`, always installed by `create_app()`.

    Args:
        request: The current request, used to reach `app.state.cache`.

    Returns:
        The `TTLCache` instance `create_app()` was built with (or its default
        `InMemoryTTLCache` when no `cache=` kwarg was passed).
    """
    cache: TTLCache = request.app.state.cache
    return cache


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


async def require_signature(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Raise `SignatureError` unless the raw request body carries a valid `X-Signature`.

    Reads the raw body itself, rather than depending on the parsed body model, so the signature
    check never depends on the body being valid JSON. `api/routes/alerts.py::SignedRoute` calls
    this directly, ahead of FastAPI's own body parsing — which is what actually guarantees
    401-before-422 for malformed JSON, since FastAPI decodes the JSON body before solving
    `Depends` and a bare `Depends(require_signature)` alone could not do it. This is the one
    function that verifies the signature; `tests/test_require_signature.py` pins it directly.

    Args:
        request: The current request; used to read the raw body and the `X-Signature` header.
        settings: The app's `Settings`, for `ingest_hmac_secret`.

    Raises:
        SignatureError: When the signature is missing or does not verify.
    """
    body = await request.body()
    header = request.headers.get(SIGNATURE_HEADER)
    if not verify_signature(settings.ingest_hmac_secret.get_secret_value(), body, header):
        raise SignatureError("missing or invalid signature")
