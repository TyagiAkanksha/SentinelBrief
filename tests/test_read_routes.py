"""Pins the unsigned public read router: `GET /api/v1/alerts`, `/alerts/{id}`, `/stats`
(PRD §8, §10.1) — m3 task-02.

Every route test builds its own app via `_build_app`, which wires exactly the app the brief
specifies: `create_app(session_factory=db_session_factory, settings=Settings(
ingest_hmac_secret=SecretStr("test-secret"), alerts_list_cache_ttl_s=7, stats_cache_ttl_s=3),
cache=InMemoryTTLCache(clock=fake_clock))`, where `fake_clock` is a closure over a mutable
one-element list the test advances directly (CONVENTIONS.md §10: the clock is an injectable
seam) — never `time.sleep`. Seeded rows always go through `tests.helpers.seed_alert` /
`add_verdict`, the production writers, and every seed commits before the following GET, so the
read routes see exactly what a real ingest + triage run would have produced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from api.factory import create_app
from api.routes.alerts_read import _cached_json, cache_key
from core.cache import InMemoryTTLCache
from core.config import Settings
from core.schemas.verdict import Verdict, VerdictCategory
from tests.helpers import add_verdict, seed_alert
from worker.store import ToolCallRecord

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROUTES_DIR = _REPO_ROOT / "api" / "routes"

# The exact three-alert seed `test_list_alerts_filters_are_wired` uses: alert A alone satisfies
# `severity_gte=4` / `category=brute_force` / `escalate=true`; alert C alone lands at/after
# `since=T+30m`. Module-level so both the parametrize decorator and the test body see one value.
_FILTER_T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _verdict(
    *,
    severity: int = 3,
    category: VerdictCategory = "scanning",
    escalate: bool = False,
    confidence: float = 0.7,
) -> Verdict:
    """A `Verdict` with sensible defaults, overridable per test."""
    return Verdict(
        severity=severity,
        category=category,
        confidence=confidence,
        reasoning="test reasoning",
        recommended_action="monitor",
        escalate=escalate,
    )


def _build_app(session_factory: async_sessionmaker[AsyncSession]) -> tuple[FastAPI, list[float]]:
    """The brief's exact app: `alerts_list_cache_ttl_s=7`, `stats_cache_ttl_s=3`, an injected
    clock. Returns the app and the mutable clock box the test advances."""
    clock_box = [0.0]

    def fake_clock() -> float:
        return clock_box[0]

    app = create_app(
        session_factory=session_factory,
        settings=Settings(
            ingest_hmac_secret=SecretStr("test-secret"),
            alerts_list_cache_ttl_s=7,
            stats_cache_ttl_s=3,
        ),
        cache=InMemoryTTLCache(clock=fake_clock),
    )
    return app, clock_box


async def _seed(
    session_factory: async_sessionmaker[AsyncSession], name: str = "alert4", **kwargs: object
) -> uuid.UUID:
    """Seed one alert (`tests.helpers.seed_alert`) from a fresh session, committing before it
    returns."""
    async with session_factory() as session:
        alert_id = await seed_alert(session, name, **kwargs)
        await session.commit()
        return alert_id


async def test_list_alerts_returns_paginated_envelope(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)
    await _seed(
        db_session_factory,
        "alert4",
        session_id="envelope-1",
        verdict=_verdict(severity=4, escalate=True),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/alerts")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size"}
    assert body["total"] == 1
    item = body["items"][0]
    assert item["src_ip"] == "192.0.2.55"
    assert item["verdict"]["severity"] == 4
    assert "reasoning_excerpt" in item["verdict"]


@pytest.mark.parametrize(
    "query",
    [
        {"severity_gte": "4"},
        {"category": "brute_force"},
        {"escalate": "true"},
        {"since": (_FILTER_T0 + timedelta(minutes=30)).isoformat()},
    ],
    ids=["severity_gte", "category", "escalate", "since"],
)
async def test_list_alerts_filters_are_wired(
    db_session_factory: async_sessionmaker[AsyncSession], query: dict[str, str]
) -> None:
    app, _clock = _build_app(db_session_factory)
    await _seed(
        db_session_factory,
        session_id="filters-a",
        verdict=_verdict(severity=5, category="brute_force", escalate=True),
        received_at=_FILTER_T0,
    )
    await _seed(
        db_session_factory,
        session_id="filters-b",
        verdict=_verdict(severity=2, category="scanning", escalate=False),
        received_at=_FILTER_T0 + timedelta(minutes=15),
    )
    await _seed(
        db_session_factory,
        session_id="filters-c",
        verdict=_verdict(severity=1, category="reconnaissance", escalate=False),
        received_at=_FILTER_T0 + timedelta(minutes=45),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/alerts", params=query)

    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    "query",
    [
        {"page": "0"},
        {"page_size": "101"},
        {"severity_gte": "6"},
        {"category": "bogus"},
        {"since": "yesterday"},
    ],
    ids=["page", "page_size", "severity_gte", "category", "since"],
)
async def test_list_alerts_422_envelope_for_invalid_query(
    db_session_factory: async_sessionmaker[AsyncSession], query: dict[str, str]
) -> None:
    app, _clock = _build_app(db_session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/alerts", params=query)

    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == "validation_error"


async def test_list_alerts_cached_until_alerts_list_cache_ttl_s(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="ttl-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/alerts")
        assert first.json()["total"] == 1

        await _seed(db_session_factory, session_id="ttl-2")

        clock[0] = 6.0
        still_cached = await client.get("/api/v1/alerts")
        assert still_cached.json()["total"] == 1

        clock[0] = 7.0
        fresh = await client.get("/api/v1/alerts")
        assert fresh.json()["total"] == 2


async def test_list_alerts_cache_key_normalizes_query_param_order(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="key-order-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/alerts?page=1&page_size=5")
        assert first.json()["total"] == 1

        await _seed(db_session_factory, session_id="key-order-2")

        second = await client.get("/api/v1/alerts?page_size=5&page=1")

    # Same normalized cache key as the first request -> a stale (cached) total, not 2.
    assert second.json()["total"] == 1


async def test_list_alerts_undeclared_query_params_share_the_declared_key(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Controller ruling (m3 task-02 review I3): `cache_key` is built only from the route's
    declared, validated params, so an undeclared query param (`?zzz=...`) can never mint a new
    cache entry — the key space stays bounded by the declared parameter domain, not by whatever
    an unauthenticated caller appends to the query string."""
    app, _clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="undeclared-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/alerts")
        assert first.json()["total"] == 1

        await _seed(db_session_factory, session_id="undeclared-2")

        zzz1 = await client.get("/api/v1/alerts", params={"zzz": "1"})
        zzz2 = await client.get("/api/v1/alerts", params={"zzz": "2"})

    # Same declared-param key as the bare request -> both stay stale at 1, not a fresh 2.
    assert zzz1.json()["total"] == 1
    assert zzz2.json()["total"] == 1


def test_cache_key_is_built_from_declared_params_only() -> None:
    """DB-less unit test of `cache_key`'s new `(path, params)` shape (m3 task-02 review I3)."""
    key = cache_key(
        "/api/v1/alerts",
        {
            "page": 1,
            "page_size": 25,
            "severity_gte": None,
            "since": datetime(2026, 9, 1, tzinfo=UTC),
            "escalate": True,
        },
    )

    assert key == (
        "/api/v1/alerts?escalate=true&page=1&page_size=25&since=2026-09-01T00%3A00%3A00%2B00%3A00"
    )
    assert cache_key("/api/v1/stats", {}) == "/api/v1/stats"

    reordered_key = cache_key(
        "/api/v1/alerts",
        {
            "since": datetime(2026, 9, 1, tzinfo=UTC),
            "page_size": 25,
            "escalate": True,
            "severity_gte": None,
            "page": 1,
        },
    )
    assert reordered_key == key


def test_cache_key_keeps_false_and_zero_values() -> None:
    """m3 task-02 review N3: only `None` is dropped from the key — `False` and `0` must survive.
    Changing the drop condition from `is None` to a truthiness check (`if not value`) would fold
    `escalate=False`/a zero value into the unfiltered key without failing loudly here."""
    assert (
        cache_key("/api/v1/alerts", {"escalate": False, "page": 1})
        == "/api/v1/alerts?escalate=false&page=1"
    )
    assert cache_key("/p", {"n": 0}) == "/p?n=0"


async def test_list_alerts_escalate_false_is_a_distinct_cache_entry(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """m3 task-02 review N3 route-level (re-review round 2, N6: the original true/false pair never
    collided with the *bare* request either way, so it pinned only the filter, not the cache-key
    half of N3). GETting the bare list first caches it under the undecorated key; `?escalate=false`
    must then be a genuinely distinct cache entry, not fall back onto that cached, unfiltered body.
    Under `if value is None` -> `if not value`, `escalate=False` would drop out of the key and
    `?escalate=false` would collapse onto the bare key's cached total."""
    app, _clock = _build_app(db_session_factory)
    await _seed(
        db_session_factory,
        session_id="escfalse-true",
        verdict=_verdict(severity=4, escalate=True),
    )
    non_escalated_id = await _seed(
        db_session_factory,
        session_id="escfalse-false",
        verdict=_verdict(severity=2, escalate=False),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bare_response = await client.get("/api/v1/alerts")
        assert bare_response.json()["total"] == 2  # cached under the bare (undecorated) key

        false_response = await client.get("/api/v1/alerts", params={"escalate": "false"})

    false_body = false_response.json()
    assert false_body["total"] == 1
    assert false_body["items"][0]["id"] == str(non_escalated_id)


async def test_list_alerts_cache_hit_keeps_json_content_type(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="content-type-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/alerts")
        second = await client.get("/api/v1/alerts")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["content-type"].startswith("application/json")
    assert second.json()["total"] == 1


class _SpyCache:
    """A `TTLCache` that delegates to a real `InMemoryTTLCache` but records every `set` call
    (m3 task-02 review I1: mutation testing showed a `cache.set` inserted *before* `produce()`
    inside `_cached_json` was undetected by the previous version of this test, because that
    version only ever drove a 422 raised during FastAPI's own dependency/query validation — a
    path `_cached_json` never reaches at all. `spy.set_calls == []` catches any `set` regardless
    of when in `_cached_json` it happens; the empty-`_entries` check below additionally proves
    nothing reached the cache under *any* key, not just one hand-picked one)."""

    def __init__(self) -> None:
        self._inner = InMemoryTTLCache()
        self.set_calls: list[tuple[str, bytes, int]] = []

    async def get(self, key: str) -> bytes | None:
        return await self._inner.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self.set_calls.append((key, value, ttl_s))
        await self._inner.set(key, value, ttl_s)


async def test_list_alerts_non_2xx_never_cached() -> None:
    """m3 task-02 review I1, re-review round 2: the fix-round-1 rewrite still failed *before*
    `_cached_json` ran — `create_app(session_factory=None)` raises inside FastAPI's own dependency
    resolution (`api/deps.py::get_session`), so mutation A (`cache.set` inserted before
    `produce()` inside `_cached_json`) still left the whole suite green. This is a DB-less unit
    test of `_cached_json` itself, driving the ordering invariant directly: `produce()` raising
    must propagate out of `_cached_json` (never swallowed) with `cache.set` never having been
    reached."""
    spy = _SpyCache()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/alerts",
            "query_string": b"",
            "headers": [],
        }
    )

    async def _produce() -> BaseModel:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _cached_json(request, spy, 15, _produce, {"page": 1})

    assert spy.set_calls == []
    assert await spy.get(cache_key("/api/v1/alerts", {"page": 1})) is None


async def test_list_and_stats_cache_keys_do_not_collide(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """m3 task-02 review I2: mutation-tested — dropping `request.url.path` from `cache_key` left
    the *entire* 329-test suite green, because no test GETs both `/api/v1/alerts` and
    `/api/v1/stats` against the same app/cache instance. This one does."""
    app, _clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="no-collide-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_first = await client.get("/api/v1/alerts")
        stats_response = await client.get("/api/v1/stats")
        list_second = await client.get("/api/v1/alerts")

    list_body_1 = list_first.json()
    stats_body = stats_response.json()
    list_body_2 = list_second.json()

    assert "items" in list_body_1 and "total_alerts" not in list_body_1
    assert "total_alerts" in stats_body and "items" not in stats_body
    assert "items" in list_body_2 and "total_alerts" not in list_body_2


async def test_get_alert_returns_detail_with_verdict_and_tool_calls(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)
    tool_calls = (
        ToolCallRecord(seq=0, tool_name="check_reputation", arguments={}, result={}, latency_ms=5),
        ToolCallRecord(
            seq=1, tool_name="get_session_commands", arguments={}, result={}, latency_ms=5
        ),
    )
    async with db_session_factory() as session:
        alert_id = await seed_alert(session, "alert4", session_id="detail-route")
        await add_verdict(
            session, alert_id, _verdict(severity=5, escalate=True), tool_calls=tool_calls
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/alerts/{alert_id}")

    assert response.status_code == 200
    body = response.json()
    assert "raw" in body
    assert body["verdict"]["reasoning"] == "test reasoning"
    assert [tc["seq"] for tc in body["tool_calls"]] == [0, 1]


async def test_get_alert_404_envelope(db_session_factory: async_sessionmaker[AsyncSession]) -> None:
    app, _clock = _build_app(db_session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/alerts/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_get_alert_422_envelope_for_non_uuid(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/alerts/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_get_alert_is_never_cached(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)
    async with db_session_factory() as session:
        alert_id = await seed_alert(session, session_id="detail-uncached")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(f"/api/v1/alerts/{alert_id}")
        assert first.json()["verdict"] is None

        async with db_session_factory() as session:
            await add_verdict(session, alert_id, _verdict(severity=5, escalate=True))
            await session.commit()

        second = await client.get(f"/api/v1/alerts/{alert_id}")

    assert second.json()["verdict"]["severity"] == 5


async def test_get_stats_returns_stats_out(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, _clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="stats-1")
    await _seed(db_session_factory, session_id="stats-2", verdict=_verdict(severity=3))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "total_alerts",
        "by_status",
        "by_severity",
        "by_category",
        "escalated_count",
        "volume_by_day",
        "cost_total_usd",
        "cost_mean_usd",
        "cost_by_day",
        "latency_p50_ms",
        "latency_p95_ms",
        "last_alert_at",
        # m8b task-05 (pinned-file conflict, implementer-report-flagged): the daily token-budget
        # circuit breaker's read surface, always present on the wire (fail-open when Redis is
        # unwired, `tests/test_stats_budget.py::test_stats_fails_open_when_redis_is_unwired`).
        "budget_exhausted",
        "tokens_today",
        "daily_token_budget",
    }
    assert body["total_alerts"] == 2


async def test_get_stats_cached_until_stats_cache_ttl_s(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app, clock = _build_app(db_session_factory)
    await _seed(db_session_factory, session_id="stats-ttl-1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/stats")
        assert first.json()["total_alerts"] == 1

        await _seed(db_session_factory, session_id="stats-ttl-2")

        clock[0] = 2.0
        stale = await client.get("/api/v1/stats")
        assert stale.json()["total_alerts"] == 1

        clock[0] = 3.0
        fresh = await client.get("/api/v1/stats")
        assert fresh.json()["total_alerts"] == 2


async def test_read_router_is_unsigned(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Deferred: `api.routes.alerts_read` doesn't exist yet, and this is the only test in the file
    # that needs it directly — every other test only ever reaches it indirectly, through the app
    # (this module's own RED failure is `core.cache`, pinned by every other test's import of it).
    from fastapi import APIRouter

    from api.routes import alerts_read
    from api.routes.alerts import SignedRoute

    assert type(alerts_read.router) is APIRouter
    assert not any(isinstance(route, SignedRoute) for route in alerts_read.router.routes)

    app, _clock = _build_app(db_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/alerts")

    assert response.status_code == 200


def test_no_route_uses_bare_get_session_dependency() -> None:
    offenders = [
        path.name
        for path in sorted(_ROUTES_DIR.glob("*.py"))
        if "Depends(get_session)" in path.read_text()
    ]

    assert offenders == []
