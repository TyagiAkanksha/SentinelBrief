"""Pins `api/deps.py::SessionDep` — the request-scoped session dependency that owns
commit/rollback (CONVENTIONS.md §3, §5) — m2 task-02 fix 2.

`SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]` is the alias
routes take instead of a bare `Depends(get_session)`, so the dependency's post-yield exit code
(the commit, or the rollback on error) runs to completion **before** the response is sent —
never after, where a commit failure could otherwise be silently swallowed and the client would
see a `200` for a write that never actually persisted. `from api.deps import SessionDep` fails
today (`SessionDep` does not exist yet), which is this file's RED signal.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.deps import SessionDep
from api.factory import create_app
from core.errors import ConflictError
from core.models import AlertRow


def _alert_row(fingerprint: str) -> AlertRow:
    """Build a minimal, otherwise-valid `AlertRow` for a probe route to insert."""
    return AlertRow(
        fingerprint=fingerprint,
        source="cowrie",
        event_time=datetime.now(UTC),
        raw={},
    )


async def _fingerprint_count(
    db_session_factory: async_sessionmaker[AsyncSession], fingerprint: str
) -> int:
    """Count `alerts` rows with `fingerprint`, through a session independent of the request."""
    async with db_session_factory() as verify_session:
        count = await verify_session.scalar(
            select(func.count()).select_from(AlertRow).where(AlertRow.fingerprint == fingerprint)
        )
    assert count is not None
    return count


async def test_session_dep_commits_on_success(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=db_session_factory)
    fingerprint = secrets.token_hex(16)

    @app.post("/_probe/commit", operation_id="probe_commit")
    async def _probe(session: SessionDep) -> dict[str, bool]:
        session.add(_alert_row(fingerprint))
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/_probe/commit")

    assert response.status_code == 200
    assert await _fingerprint_count(db_session_factory, fingerprint) == 1


async def test_session_dep_rolls_back_on_error(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=db_session_factory)
    fingerprint = secrets.token_hex(16)

    @app.post("/_probe/rollback", operation_id="probe_rollback")
    async def _probe(session: SessionDep) -> None:
        session.add(_alert_row(fingerprint))
        raise ConflictError("nope")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/_probe/rollback")

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "conflict", "message": "nope"}}
    assert await _fingerprint_count(db_session_factory, fingerprint) == 0


async def test_session_dep_commit_failure_is_500_not_200(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(session_factory=db_session_factory)
    fingerprint = secrets.token_hex(16)

    @app.post("/_probe/commit_failure", operation_id="probe_commit_failure")
    async def _probe(session: SessionDep) -> dict[str, bool]:
        session.add(_alert_row(fingerprint))
        return {"ok": True}

    async def _raise_operational_error(self: AsyncSession) -> None:
        raise OperationalError("boom", None, Exception("x"))

    monkeypatch.setattr(AsyncSession, "commit", _raise_operational_error)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/_probe/commit_failure")

    # A commit that fails must never look like the write succeeded: 500, not the 200 the route
    # handler itself returned.
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}


async def test_session_dep_500_envelope_when_dbless() -> None:
    app = create_app()

    @app.post("/_probe/session_dep_dbless", operation_id="probe_session_dep_dbless")
    async def _probe(session: SessionDep) -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/_probe/session_dep_dbless")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}
