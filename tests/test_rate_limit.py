"""Pins the Redis-backed per-IP rate limiter on the public GET routers (PRD §8, §10.10; m8b
task-04).

`api/deps.py::rate_limit` does not exist yet; every test below drives the real ASGI app
(`httpx.ASGITransport`) against the wired `arq_redis` test fixture, never a mock of our own code
(CONVENTIONS.md §10). `ASGITransport(client=(ip, port))` is the one way httpx lets a test choose
`request.client.host` — the peer address `rate_limit` must key its bucket on (PRD §10.10: Caddy
overwrites `X-Forwarded-For` and the api is never host-published, so the peer IS the real client;
a forged XFF header must never mint a second bucket).

Several tests below deliberately pair a "prove the limiter is active" step with the behaviour
they actually pin (0 disables it / ingest is exempt / `/healthz` is exempt / a dead Redis fails
open): today, with no limiter wired at all, a bare "GET never 429s" assertion would pass
vacuously and prove nothing. Pairing it with a same-settings GET that DOES need to 429 keeps
every test in this file genuinely RED before `rate_limit` exists (see the test-author report's
RED evidence).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from arq.connections import ArqRedis
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from core.queue import make_redis
from tests.helpers import TEST_SECRET, fixture_body, signed_headers

# Port 1 (tcpmux) has nothing listening on a dev/CI box, so the connection is refused
# immediately instead of hanging on a timeout (mirrors tests/test_redis_cache.py's dead-Redis
# pattern) — this is "Redis DOWN", distinct from Redis simply being unwired.
_DEAD_REDIS_URL = "redis://127.0.0.1:1/0"

_FIXTURE_BODY = fixture_body("alert4")


def _fixture_body_with_session_id(session_id: str) -> bytes:
    """`_FIXTURE_BODY` with `session_id` overridden — a distinct fingerprint per call, without
    hand-rolling a whole session payload (mirrors `tests/test_ingest.py`)."""
    payload = json.loads(_FIXTURE_BODY)
    payload["session_id"] = session_id
    return json.dumps(payload).encode()


def _settings(**overrides: object) -> Settings:
    return Settings(ingest_hmac_secret=SecretStr(TEST_SECRET), **overrides)


@dataclass
class FakeEnqueue:
    """The fake of the external queue seam (`api.deps.EnqueueFn`), never our own code — mirrors
    `tests/test_ingest.py::FakeEnqueue`."""

    calls: list[uuid.UUID] = field(default_factory=list)

    async def __call__(self, alert_id: uuid.UUID) -> None:
        self.calls.append(alert_id)


async def test_over_limit_returns_429_envelope_with_retry_after(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=2),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("203.0.113.10", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/alerts")
        second = await client.get("/api/v1/alerts")
        third = await client.get("/api/v1/alerts")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    body = third.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert body["error"]["code"] == "rate_limited"
    retry_after = third.headers.get("retry-after")
    assert retry_after is not None
    assert int(retry_after) > 0


async def test_separate_ips_get_independent_buckets(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=2),
        redis=arq_redis,
    )
    ip_a = ASGITransport(app=app, client=("203.0.113.21", 1))
    ip_b = ASGITransport(app=app, client=("203.0.113.22", 1))

    async with AsyncClient(transport=ip_a, base_url="http://test") as client_a:
        a1 = await client_a.get("/api/v1/alerts")
        a2 = await client_a.get("/api/v1/alerts")
        a3 = await client_a.get("/api/v1/alerts")

    async with AsyncClient(transport=ip_b, base_url="http://test") as client_b:
        b1 = await client_b.get("/api/v1/alerts")

    assert [a1.status_code, a2.status_code, a3.status_code] == [200, 200, 429]
    # IP B's own first request must still succeed — its bucket is independent of IP A's, which
    # is already exhausted.
    assert b1.status_code == 200


async def test_forged_x_forwarded_for_does_not_create_a_separate_bucket(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """The client trusts the ASGI peer address, never `X-Forwarded-For` (PRD §10.10: Caddy
    overwrites it and the api is never host-published) — three different forged XFF values from
    ONE peer must still share one bucket."""
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=2),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("203.0.113.30", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/alerts", headers={"X-Forwarded-For": "1.2.3.4"})
        second = await client.get("/api/v1/alerts", headers={"X-Forwarded-For": "5.6.7.8"})
        third = await client.get("/api/v1/alerts", headers={"X-Forwarded-For": "9.9.9.9"})

    assert first.status_code == 200
    assert second.status_code == 200
    # If a forged XFF minted its own bucket, this would still be a fresh 200, not a 429.
    assert third.status_code == 429


async def test_rate_limit_bucket_is_shared_across_public_get_routes(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """The bucket key is `f"rl:{client_ip}:{minute_bucket}"` — no path component (task brief
    Interfaces block) — so one client's counter is shared by every public GET route, not
    reset per endpoint."""
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=3),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("203.0.113.40", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/alerts")
        second = await client.get(f"/api/v1/alerts/{uuid.uuid4()}")
        third = await client.get("/api/v1/stats")
        fourth = await client.get("/api/v1/alerts")

    assert first.status_code == 200
    assert second.status_code == 404  # allowed through the limiter; the alert just doesn't exist
    assert third.status_code == 200
    assert fourth.status_code == 429


async def test_public_get_is_limited_but_signed_ingest_post_is_exempt(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    fake_enqueue = FakeEnqueue()
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=1),
        enqueue=fake_enqueue,
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("203.0.113.50", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_first = await client.get("/api/v1/alerts")
        get_second = await client.get("/api/v1/alerts")

        first_body = _fixture_body_with_session_id("rl-ingest-1")
        second_body = _fixture_body_with_session_id("rl-ingest-2")
        post_first = await client.post(
            "/api/v1/alerts", content=first_body, headers=signed_headers(TEST_SECRET, first_body)
        )
        post_second = await client.post(
            "/api/v1/alerts",
            content=second_body,
            headers=signed_headers(TEST_SECRET, second_body),
        )

    # Proves the limiter is genuinely active at this IP/setting (never a vacuous pass).
    assert get_first.status_code == 200
    assert get_second.status_code == 429
    # The signed ingest POST is exempt: it succeeds twice from the very same, already-capped IP.
    assert post_first.status_code == 202
    assert post_second.status_code == 202


async def test_healthz_is_exempt_from_the_public_rate_limit(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=1),
        redis=arq_redis,
    )
    transport = ASGITransport(app=app, client=("203.0.113.60", 1))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_first = await client.get("/api/v1/alerts")
        get_second = await client.get("/api/v1/alerts")
        health_responses = [await client.get("/healthz") for _ in range(3)]

    # Proves the limiter is genuinely active at this IP/setting (never a vacuous pass).
    assert get_first.status_code == 200
    assert get_second.status_code == 429
    # /healthz never 429s from the very same, already-capped IP.
    assert all(response.status_code != 429 for response in health_responses)


async def test_public_rate_limit_per_min_zero_disables_the_limiter(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    limited_app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=1),
        redis=arq_redis,
    )
    unlimited_app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=0),
        redis=arq_redis,
    )
    limited_transport = ASGITransport(app=limited_app, client=("203.0.113.70", 1))
    # A distinct peer for the unlimited app so its bucket (if the limiter were mistakenly still
    # active) could never be mistaken for the limited app's already-exhausted one.
    unlimited_transport = ASGITransport(app=unlimited_app, client=("203.0.113.71", 1))

    async with AsyncClient(transport=limited_transport, base_url="http://test") as client:
        limited_first = await client.get("/api/v1/alerts")
        limited_second = await client.get("/api/v1/alerts")

    async with AsyncClient(transport=unlimited_transport, base_url="http://test") as client:
        unlimited_responses = [await client.get("/api/v1/alerts") for _ in range(10)]

    # Proves the limiter is genuinely active at limit=1 (never a vacuous pass).
    assert limited_first.status_code == 200
    assert limited_second.status_code == 429
    # limit=0 never 429s, no matter how many requests the same test sends.
    assert all(response.status_code == 200 for response in unlimited_responses)


async def test_redis_down_fails_open_get_still_succeeds(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    live_app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=1),
        redis=arq_redis,
    )
    dead_redis = make_redis(_DEAD_REDIS_URL, socket_timeout_s=1.0)
    dead_app = create_app(
        session_factory=db_session_factory,
        settings=_settings(public_rate_limit_per_min=1),
        redis=dead_redis,
    )
    live_transport = ASGITransport(app=live_app, client=("203.0.113.80", 1))
    dead_transport = ASGITransport(app=dead_app, client=("203.0.113.81", 1))

    try:
        async with AsyncClient(transport=live_transport, base_url="http://test") as client:
            live_first = await client.get("/api/v1/alerts")
            live_second = await client.get("/api/v1/alerts")

        async with AsyncClient(transport=dead_transport, base_url="http://test") as client:
            dead_first = await client.get("/api/v1/alerts")
            dead_second = await client.get("/api/v1/alerts")
    finally:
        await dead_redis.aclose()

    # Proves the limiter is genuinely active against a live Redis (never a vacuous pass).
    assert live_first.status_code == 200
    assert live_second.status_code == 429
    # An unreachable Redis must never 500 (or 429) a public GET — fail open instead.
    assert dead_first.status_code == 200
    assert dead_second.status_code == 200
