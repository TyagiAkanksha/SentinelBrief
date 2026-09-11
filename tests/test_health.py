"""Pins `GET /healthz` (PRD §8; CONVENTIONS.md §4's one documented try/except carve-out) —
m2 task-02, extended at m5 task-05 with the Redis probe.

Two independent probes, both always run and both always reported: `db` (`SELECT 1` through the
wired session factory) and `redis` (`PING` through `app.state.redis`). `status` is `"ok"` (`200`)
only when both answer; otherwise `"degraded"` (`503`), naming the failing side (`"error"`) or
`"unconfigured"` when the seam was never wired at all. A liveness probe must never look like an
application crash (a `500` envelope), which is why `/healthz` alone is allowed to catch DB/Redis
errors — this is what makes the compose `HEALTHCHECK` turn the api unhealthy the moment Redis
goes away (PRD §8).
"""

from __future__ import annotations

import time

from arq.connections import ArqRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.db import make_engine, make_session_factory
from core.queue import make_redis


async def test_healthz_unconfigured_both() -> None:
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "db": "unconfigured",
        "redis": "unconfigured",
    }


async def test_healthz_ok_when_db_and_redis_answer(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    app = create_app(session_factory=db_session_factory, redis=arq_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


async def test_healthz_503_when_redis_down(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Port 1 (tcpmux) has nothing listening on a dev/CI box, so the connection is refused
    # immediately instead of hanging on a timeout.
    dead_redis = make_redis("redis://127.0.0.1:1/0", socket_timeout_s=0.5)
    try:
        app = create_app(session_factory=db_session_factory, redis=dead_redis)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "ok", "redis": "error"}
    finally:
        await dead_redis.aclose()


async def test_healthz_503_when_db_down(arq_redis: ArqRedis) -> None:
    # Port 1 (tcpmux) has nothing listening on a dev/CI box, so the connection is refused
    # immediately instead of hanging on a timeout.
    engine = make_engine("postgresql://sentinel:sentinel@127.0.0.1:1/nope")
    try:
        session_factory = make_session_factory(engine)
        app = create_app(session_factory=session_factory, redis=arq_redis)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "degraded", "db": "error", "redis": "ok"}
    finally:
        await engine.dispose()


async def test_healthz_redis_error_answers_within_the_socket_timeout(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 10.255.255.1 is a non-routable address (nothing forwards to it): the connect attempt hangs
    # until the socket_timeout fires, rather than being refused immediately like the
    # `127.0.0.1:1` cases above — this is what pins the timeout is actually applied, not merely
    # that a *refused* connection answers fast.
    dead_redis = make_redis("redis://10.255.255.1:6379/0", socket_timeout_s=0.5)
    try:
        app = create_app(session_factory=db_session_factory, redis=dead_redis)

        started = time.perf_counter()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/healthz")
        elapsed = time.perf_counter() - started

        assert response.status_code == 503
        assert elapsed < 3.0  # generous bound: pins that the timeout is applied at all (rule 7)
    finally:
        await dead_redis.aclose()
