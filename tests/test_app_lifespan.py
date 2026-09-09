"""Pins `create_app`'s ASGI lifespan (CONVENTIONS.md §5) — m5 task-01 fix-1, review finding I2.

`httpx.ASGITransport` (every other test in the suite drives the app through it) never runs the
ASGI lifespan protocol, so nothing else in the suite exercises `api/factory.py`'s
`lifespan(app)`: the shutdown branch that `aclose()`s the wired Redis client was unpinned. Driven
directly through `app.router.lifespan_context(app)` instead.
"""

from __future__ import annotations

from typing import cast

from redis.asyncio import Redis

from api.factory import create_app


async def test_lifespan_closes_the_wired_redis_client_on_shutdown() -> None:
    """`create_app`'s lifespan must `aclose()` the wired Redis client on shutdown (m5 task-01
    Interfaces). `httpx.ASGITransport` never runs the ASGI lifespan, so nothing else covers it."""
    closed: list[str] = []

    class _RecordingRedis:
        async def aclose(self) -> None:
            closed.append("aclose")

    app = create_app(redis=cast(Redis, _RecordingRedis()))
    async with app.router.lifespan_context(app):
        pass

    assert closed == ["aclose"]


async def test_lifespan_is_a_no_op_when_no_redis_is_wired() -> None:
    """DB-less/Redis-less construction must still start and stop cleanly (CONVENTIONS.md §5)."""
    app = create_app()
    async with app.router.lifespan_context(app):
        pass
