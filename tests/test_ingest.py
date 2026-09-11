"""Pins `POST /api/v1/alerts`: signature-before-parse, dedup, insert-commit-enqueue-only
(PRD §6.1, §3, §8) — m2 task-03; rewritten at m5 task-01 (queue split, spine M5-a).

`require_signature` reads the raw body itself and is declared before the `SessionAlert` body
parameter, so a bad signature is `401` even for garbage JSON
(`test_signature_checked_before_body_validation`) — the signature check must never depend on the
body being valid JSON.

As of this task the route does exactly three things for a newly created alert: insert, commit,
enqueue — never an LLM call, never a tool, never a verdict (PRD §3). `FakeEnqueue` is the fake of
the external queue seam (`api.deps.EnqueueFn`); it is never a `worker.jobs.triage_alert_job`
double, because nothing on the request path calls the job at all any more. A duplicate POST of a
still-`pending` alert re-enqueues (idempotent at the queue by job id — a real dead-letter proof
lives in `tests/test_queue.py`); a duplicate of an already-triaged/failed alert never enqueues
again (PRD §6.1: duplicates never re-trigger triage). Spine M5-a (`test_enqueue_sees_the_committed
_row`) pins that the route's own `commit()` runs strictly before `enqueue` — the whole reason the
route still commits explicitly instead of relying on `SessionDep`'s post-response commit.
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass, field
from time import perf_counter

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from api.routes.alerts import SignedRoute, router
from core.config import Settings
from core.errors import QueueUnavailableError
from core.models import AlertRow, AlertStatus
from core.services.alerts import set_alert_status
from core.signing import SIGNATURE_HEADER
from tests.helpers import TEST_SECRET, count_rows_fresh, fixture_body, signed_headers

_FIXTURE_BODY = fixture_body("alert4")


def _fixture_body_with_session_id(session_id: str) -> bytes:
    """`_FIXTURE_BODY` with `session_id` overridden — a distinct fingerprint per call, without
    hand-rolling a whole session payload."""
    payload = json.loads(_FIXTURE_BODY)
    payload["session_id"] = session_id
    return json.dumps(payload).encode()


@dataclass
class FakeEnqueue:
    """Records every `alert_id` it is invoked with — the fake of the external queue seam
    (`api.deps.EnqueueFn`), never our own code.

    `error`, when set, is raised (after recording the call) so
    `test_queue_unavailable_is_503_and_the_row_persists` can simulate a dead Redis without a real
    one. `session_factory`, when set, opens a **fresh** session on every call and appends the
    row's status as read from it to `observed` — the row is only ever visible there if the
    route's own `commit()` already ran (spine M5-a).
    """

    calls: list[uuid.UUID] = field(default_factory=list)
    error: Exception | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    observed: list[AlertStatus | None] = field(default_factory=list)

    async def __call__(self, alert_id: uuid.UUID) -> None:
        self.calls.append(alert_id)
        if self.session_factory is not None:
            async with self.session_factory() as session:
                row = await session.get(AlertRow, alert_id)
                self.observed.append(row.status if row is not None else None)
        if self.error is not None:
            raise self.error


@pytest.fixture
def fake_enqueue() -> FakeEnqueue:
    return FakeEnqueue()


async def test_unsigned_post_401(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts",
            content=_FIXTURE_BODY,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_bad_signature_401(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)
    headers = {SIGNATURE_HEADER: "sha256=" + "b" * 64, "content-type": "application/json"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_non_ascii_signature_header_is_401_not_500(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    """A byte >= 0x80 in `X-Signature` (Starlette decodes headers as latin-1) must still be a
    clean 401, never the 500 `hmac.compare_digest` raises on non-ASCII `str` operands
    (m2-final-review.md I2).
    """
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)
    headers = [
        (b"x-signature", b"sha256=" + b"\xe9" * 64),
        (b"content-type", b"application/json"),
    ]

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_invalid_payload_422(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)
    body = b'{"source":"cowrie"}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=body, headers=signed_headers(TEST_SECRET, body)
        )

    assert response.status_code == 422
    payload = response.json()
    # Envelope shape only — the 422 handler never echoes the request input (CONVENTIONS.md §4);
    # "cowrie" may legitimately appear inside a validation-error loc/message, but the envelope
    # itself carries exactly {"error": {"code", "message"}}.
    assert set(payload.keys()) == {"error"}
    assert set(payload["error"].keys()) == {"code", "message"}
    assert payload["error"]["code"] == "validation_error"


async def test_signature_checked_before_body_validation(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)
    bad_json = b"{not json"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unsigned = await client.post(
            "/api/v1/alerts", content=bad_json, headers={"content-type": "application/json"}
        )
        wrong_secret = await client.post(
            "/api/v1/alerts",
            content=bad_json,
            headers=signed_headers("wrong-secret", bad_json),
        )

    assert unsigned.status_code == 401
    assert wrong_secret.status_code == 401


def test_signed_router_holds_only_the_ingest_post() -> None:
    """`router` (`route_class=SignedRoute`) must hold exactly the ingest `POST` — never a `GET`.

    Any route added to this router demands a signature, since `SignedRoute` checks it ahead of
    everything else; M3's read routes (`GET /api/v1/alerts/...`) must live on their own,
    unsigned router rather than being added here.
    """
    routes = [r for r in router.routes if isinstance(r, APIRoute)]

    assert [(r.path, sorted(r.methods)) for r in routes] == [("/alerts", ["POST"])]
    assert all(isinstance(r, SignedRoute) for r in routes)


async def test_signed_post_202_pending_and_enqueues_once(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts",
            content=_FIXTURE_BODY,
            headers=signed_headers(TEST_SECRET, _FIXTURE_BODY),
        )

    assert response.status_code == 202
    body = response.json()
    alert_id = uuid.UUID(body["id"])
    assert body["status"] == "pending"
    assert body["created"] is True
    assert await count_rows_fresh(db_session_factory, AlertRow) == 1
    assert fake_enqueue.calls == [alert_id]

    async with db_session_factory() as session:
        row = await session.get(AlertRow, alert_id)
    assert row is not None
    assert row.status == "pending"


async def test_duplicate_of_a_pending_alert_re_enqueues(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)
    headers = signed_headers(TEST_SECRET, _FIXTURE_BODY)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)
        second = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["created"] is False
    assert second.json()["status"] == "pending"
    assert await count_rows_fresh(db_session_factory, AlertRow) == 1

    alert_id = uuid.UUID(first.json()["id"])
    assert fake_enqueue.calls == [alert_id, alert_id]


async def test_duplicate_of_a_triaged_alert_does_not_enqueue(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_enqueue: FakeEnqueue,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake_enqueue)
    headers = signed_headers(TEST_SECRET, _FIXTURE_BODY)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)
        alert_id = uuid.UUID(first.json()["id"])

        async with db_session_factory() as session:
            await set_alert_status(session, alert_id, "triaged")
            await session.commit()

        second = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "triaged"
    assert len(fake_enqueue.calls) == 1


async def test_enqueue_sees_the_committed_row(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Spine M5-a: `enqueue` must run only after the route's own `commit()`. Mutation: dropping
    that commit would flip `observed` from `["pending"]` to `[None]`, since the fresh session
    `FakeEnqueue.__call__` opens would not yet see a row nobody committed.
    """
    fake = FakeEnqueue(session_factory=db_session_factory)
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/alerts",
            content=_FIXTURE_BODY,
            headers=signed_headers(TEST_SECRET, _FIXTURE_BODY),
        )

    assert fake.observed == ["pending"]


async def test_queue_unavailable_is_503_and_the_row_persists(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    fake = FakeEnqueue(error=QueueUnavailableError("down"))
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts",
            content=_FIXTURE_BODY,
            headers=signed_headers(TEST_SECRET, _FIXTURE_BODY),
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "queue_unavailable"

    assert await count_rows_fresh(db_session_factory, AlertRow) == 1
    async with db_session_factory() as session:
        row = (await session.execute(select(AlertRow))).scalar_one()
    assert row.status == "pending"


async def test_ingest_route_latency_under_budget_with_fake_queue(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Pins PRD §3 / m5 spine: `POST /api/v1/alerts` never awaits an LLM or a tool on the request
    path — only the insert, the commit, and an immediately-returning `enqueue` (a fake here; the
    live acceptance burst against real Redis is what measures the actual round trip). One
    untimed warm-up POST, then 5 timed POSTs of distinct `session_id`s: median under 100 ms. If
    this flakes, the fix is a bigger sample, never a bigger budget (m4 plan-defect rule 14).
    """
    fake = FakeEnqueue()
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=fake)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        warmup_body = _fixture_body_with_session_id("latency-warmup")
        await client.post(
            "/api/v1/alerts",
            content=warmup_body,
            headers=signed_headers(TEST_SECRET, warmup_body),
        )

        timings_ms: list[float] = []
        for i in range(5):
            body = _fixture_body_with_session_id(f"latency-{i}")
            start = perf_counter()
            response = await client.post(
                "/api/v1/alerts", content=body, headers=signed_headers(TEST_SECRET, body)
            )
            timings_ms.append((perf_counter() - start) * 1000)
            assert response.status_code == 202

    assert statistics.median(timings_ms) < 100
