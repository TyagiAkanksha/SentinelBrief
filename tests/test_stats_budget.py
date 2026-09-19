"""Pins `StatsOut`'s daily-token-budget fields (`budget_exhausted`, `tokens_today`,
`daily_token_budget`) and their FAIL-OPEN behavior when Redis is unwired (m8b task-05 brief,
Interfaces block: "class StatsOut: ...; budget_exhausted: bool; tokens_today: int;
daily_token_budget: int"). Driven through the real ASGI app (`GET /api/v1/stats`), never the
service function directly -- its exact new signature is an implementation detail the brief leaves
to the implementer (Steps: "implementer: ... StatsOut + stats service ..."), mirroring
`api/deps.py::rate_limit`'s own established fail-open-when-unwired contract (m8b task-04).

`worker.budget.record_tokens` (also new this task) seeds the Redis counter the same way the
worker itself will, so this file never hand-rolls the Redis key format. `StatsOut` carries no
budget field yet, so every test below fails RED today with a `KeyError` on the missing response
keys (or a `ModuleNotFoundError` importing `worker.budget`).
"""

from __future__ import annotations

from arq.connections import ArqRedis
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from tests.helpers import TEST_SECRET
from worker.budget import record_tokens


def _settings(**overrides: object) -> Settings:
    return Settings(ingest_hmac_secret=SecretStr(TEST_SECRET), **overrides)


async def test_stats_reports_budget_exhausted_when_todays_tokens_meet_the_budget(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    await record_tokens(arq_redis, tokens=100)
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(daily_token_budget=100),
        redis=arq_redis,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["budget_exhausted"] is True
    assert body["tokens_today"] == 100
    assert body["daily_token_budget"] == 100


async def test_stats_reports_not_exhausted_when_under_budget(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    await record_tokens(arq_redis, tokens=40)
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(daily_token_budget=1000),
        redis=arq_redis,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["budget_exhausted"] is False
    assert body["tokens_today"] == 40
    assert body["daily_token_budget"] == 1000


async def test_stats_budget_zero_is_never_exhausted_even_with_a_huge_counter(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    await record_tokens(arq_redis, tokens=999_999)
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(daily_token_budget=0),
        redis=arq_redis,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["budget_exhausted"] is False
    assert body["daily_token_budget"] == 0


async def test_stats_fails_open_when_redis_is_unwired(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(daily_token_budget=500),
        redis=None,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["budget_exhausted"] is False
    assert body["tokens_today"] == 0
    assert body["daily_token_budget"] == 500
