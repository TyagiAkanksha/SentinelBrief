"""Pins `GET /healthz` (PRD §8; CONVENTIONS.md §4's one documented try/except carve-out) —
m2 task-02.

Three branches: no `session_factory` wired at all -> 503 "unconfigured"; a real, reachable
database (`db_session_factory`, throwaway schema) -> 200 "ok"; a `session_factory` bound to a
database that refuses the connection -> 503 "error". A liveness probe must never look like an
application crash (a `500` envelope), which is why `/healthz` alone is allowed to catch DB errors.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.db import make_engine, make_session_factory


async def test_healthz_503_when_db_unconfigured() -> None:
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "unconfigured"}


async def test_healthz_ok_pings_db(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=db_session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_healthz_503_when_db_down() -> None:
    # Port 1 (tcpmux) has nothing listening on a dev/CI box, so the connection is refused
    # immediately instead of hanging on a timeout.
    engine = make_engine("postgresql://sentinel:sentinel@127.0.0.1:1/nope")
    try:
        session_factory = make_session_factory(engine)
        app = create_app(session_factory=session_factory)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "error"}
    finally:
        await engine.dispose()
