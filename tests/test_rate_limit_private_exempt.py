"""Pins `api/deps.py::rate_limit`'s private-IP exemption (m8b whole-repo review, finding I2,
ruling R-M8b-7): a peer at a loopback/RFC-1918-private address is never limited, while a real
PUBLIC peer still is. This is the fix for the server-rendered-page (web-container) shared-bucket
blackout — in production the dashboard's page-load API calls all arrive from the web container's
one compose-internal IP with no `X-Forwarded-For`, so without this exemption every visitor shared
one bucket.

Mirrors `tests/test_rate_limit.py`'s own pattern (real ASGI app over `httpx.ASGITransport`, the
wired `arq_redis` fixture, never a mock of our own code — CONVENTIONS.md §10) and, per that file's
own convention, pairs each exemption assertion with a same-settings request that DOES still 429,
so a vacuously-always-200 bug could never pass silently.
"""

from __future__ import annotations

from arq.connections import ArqRedis
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from tests.helpers import TEST_SECRET


def _settings(**overrides: object) -> Settings:
    return Settings(ingest_hmac_secret=SecretStr(TEST_SECRET), **overrides)


async def test_private_ip_client_is_never_rate_limited(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """A `10.0.0.5` peer (the web container's own compose-internal address shape) sails past the
    limit with no 429 — proven against a limit low enough that a public IP would already have
    tripped it several times over."""
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=2),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("10.0.0.5", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [await client.get("/api/v1/alerts") for _ in range(10)]

    assert all(response.status_code == 200 for response in responses)


async def test_loopback_client_is_never_rate_limited(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """`127.0.0.1` (e.g. a health check or an unproxied local caller) is exempt the same way."""
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=2),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("127.0.0.1", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [await client.get("/api/v1/alerts") for _ in range(10)]

    assert all(response.status_code == 200 for response in responses)


async def test_public_ip_client_is_still_rate_limited(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """Sanity: a real public peer (a TEST-NET-3 `203.0.113.x` address, same as
    `tests/test_rate_limit.py`) is NOT exempt — the exemption is peer-specific, not a global
    kill switch, and the pinned public-IP tests keep meaning something after this change."""
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=2),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("203.0.113.90", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/alerts")
        second = await client.get("/api/v1/alerts")
        third = await client.get("/api/v1/alerts")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
