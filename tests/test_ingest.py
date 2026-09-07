"""Pins `POST /api/v1/alerts`: signature-before-parse, dedup, triage-on-create only
(PRD §6.1, §8) — m2 task-03.

`require_signature` reads the raw body itself and is declared before the `SessionAlert` body
parameter, so a bad signature is `401` even for garbage JSON
(`test_signature_checked_before_body_validation`) — the signature check must never depend on the
body being valid JSON. Duplicates must never re-invoke triage
(`test_duplicate_post_does_not_invoke_triage`): the whole point of the fingerprint dedup is that a
shipper retry never double-bills an LLM call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from api.routes.alerts import SignedRoute, router
from core.config import Settings
from core.models import AlertRow, AlertStatus
from core.services.alerts import set_alert_status
from core.signing import SIGNATURE_HEADER, sign_body

_FIXTURE_BODY = (
    Path(__file__).resolve().parent.parent / "fixtures" / "alerts" / "alert4.json"
).read_bytes()
_SECRET = "test-secret"  # matches the `settings` fixture's `ingest_hmac_secret`


@dataclass
class FakeTriage:
    """Records every `alert_id` it is invoked with; returns a fixed `AlertStatus`.

    A `TriageFn` persists the status it returns (task-04's real `triage_alert` owns verdict +
    status in one transaction); the fake mirrors that so the route never writes status itself.
    """

    status: AlertStatus = "triaged"
    calls: list[uuid.UUID] = field(default_factory=list)

    async def __call__(self, session: AsyncSession, alert_id: uuid.UUID) -> AlertStatus:
        self.calls.append(alert_id)
        await set_alert_status(session, alert_id, self.status)
        await session.commit()
        return self.status


@pytest.fixture
def fake_triage() -> FakeTriage:
    return FakeTriage()


def _signed_headers(body: bytes, *, secret: str = _SECRET) -> dict[str, str]:
    return {SIGNATURE_HEADER: sign_body(secret, body), "content-type": "application/json"}


async def _count_alerts(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(AlertRow))
    assert count is not None
    return count


async def test_unsigned_post_401(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)

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
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)
    headers = {SIGNATURE_HEADER: "sha256=" + "b" * 64, "content-type": "application/json"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_signed_post_202_and_inserts_row(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=_FIXTURE_BODY, headers=_signed_headers(_FIXTURE_BODY)
        )

    assert response.status_code == 202
    body = response.json()
    alert_id = uuid.UUID(body["id"])
    assert body["status"] == "triaged"
    assert body["created"] is True
    assert await _count_alerts(db_session_factory) == 1
    assert fake_triage.calls == [alert_id]


async def test_duplicate_post_200_same_id_no_new_row(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)
    headers = _signed_headers(_FIXTURE_BODY)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)
        second = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["created"] is False
    assert second.json()["status"] == "triaged"
    assert await _count_alerts(db_session_factory) == 1


async def test_invalid_payload_422(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)
    body = b'{"source":"cowrie"}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/alerts", content=body, headers=_signed_headers(body))

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
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)
    bad_json = b"{not json"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unsigned = await client.post(
            "/api/v1/alerts", content=bad_json, headers={"content-type": "application/json"}
        )
        wrong_secret = await client.post(
            "/api/v1/alerts",
            content=bad_json,
            headers=_signed_headers(bad_json, secret="wrong-secret"),
        )

    assert unsigned.status_code == 401
    assert wrong_secret.status_code == 401


async def test_duplicate_post_does_not_invoke_triage(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake_triage: FakeTriage,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=settings, triage=fake_triage)
    headers = _signed_headers(_FIXTURE_BODY)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)
        await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert len(fake_triage.calls) == 1


async def test_signed_post_status_reflects_triage_outcome(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    failing_triage = FakeTriage(status="failed")
    app = create_app(session_factory=db_session_factory, settings=settings, triage=failing_triage)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=_FIXTURE_BODY, headers=_signed_headers(_FIXTURE_BODY)
        )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"


def test_signed_router_holds_only_the_ingest_post() -> None:
    """`router` (`route_class=SignedRoute`) must hold exactly the ingest `POST` — never a `GET`.

    Any route added to this router demands a signature, since `SignedRoute` checks it ahead of
    everything else; M3's read routes (`GET /api/v1/alerts/...`) must live on their own,
    unsigned router rather than being added here.
    """
    routes = [r for r in router.routes if isinstance(r, APIRoute)]

    assert [(r.path, sorted(r.methods)) for r in routes] == [("/alerts", ["POST"])]
    assert all(isinstance(r, SignedRoute) for r in routes)
