"""Pins the M3 carry-over invariant "a cache hit does no database work" (M3 final review N-M3;
m5 task-05 Goal) — `api/routes/alerts_read.py::_cached_json`'s early return on a cache hit means
`SessionDep` opens an `AsyncSession` for that request but the underlying connection pool never
hands out a real DB connection (a `sqlalchemy.pool` "checkout" event) when the response comes
straight from the `TTLCache`.

Uses a real throwaway-schema database (`db_engine`/`db_session_factory`, m2 task-01) and a real
`InMemoryTTLCache` (`core.cache`, m3 task-02) — never a mock of our own code
(CONVENTIONS.md §10). Expected GREEN on arrival: this pins existing, already-correct behavior
(m5 task-05 brief, Interfaces → test table) — the test-author report mutation-proofs it in a
scratch copy by deleting `_cached_json`'s `hit = await cache.get(key)` early return.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.factory import create_app
from core.cache import InMemoryTTLCache
from core.config import Settings
from core.schemas.verdict import Verdict
from tests.helpers import seed_alert


async def test_list_and_stats_cache_hits_check_out_no_connection(
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    verdict = Verdict(
        severity=2,
        category="scanning",
        confidence=0.8,
        reasoning="synthetic test reasoning citing session evidence.",
        recommended_action="synthetic recommended action, distinct from the reasoning text.",
        escalate=False,
    )
    await seed_alert(db_session, "alert4", verdict=verdict)
    await db_session.commit()

    checkouts = 0

    def _count_checkout(*args: object) -> None:
        nonlocal checkouts
        checkouts += 1

    event.listen(db_engine.sync_engine, "checkout", _count_checkout)
    try:
        app = create_app(
            session_factory=db_session_factory, settings=Settings(), cache=InMemoryTTLCache()
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_alerts = await client.get("/api/v1/alerts")
            first_stats = await client.get("/api/v1/stats")
            after_first_pair = checkouts

            second_alerts = await client.get("/api/v1/alerts")
            second_stats = await client.get("/api/v1/stats")
            after_second_pair = checkouts

        assert first_alerts.status_code == 200
        assert first_stats.status_code == 200
        assert second_alerts.status_code == 200
        assert second_stats.status_code == 200
        assert second_alerts.content == first_alerts.content
        assert second_stats.content == first_stats.content

        assert after_first_pair > 0  # sanity: the first pair really did do database work
        assert after_second_pair == after_first_pair  # the second pair hit the cache: no new work
    finally:
        event.remove(db_engine.sync_engine, "checkout", _count_checkout)
