"""Pins the api's ingest body cap (m6 task-02, PRD §6.1 step 1 / Global Constraint "Bound the
ingest request body"): a declared `Content-Length` over `Settings.ingest_max_body_bytes` is `413`,
a signed-route request with no usable `Content-Length` is `411` — both BEFORE `require_signature`
ever reads a byte of body — and the guard lives on `SignedRoute` only, never on an unsigned `GET`.

`FakeEnqueue` here is a local copy of `tests/test_ingest.py`'s (never imported across test files,
per the tests.md rule) — the fake of the external queue seam, never our own code.

At RED, `core.config.Settings` has no `ingest_max_body_bytes` field yet: `Settings(extra="ignore")`
silently drops the unknown kwarg rather than raising, so every HTTP-level test below fails on a
wrong status code (`401`/`202` where `413`/`411` is expected) rather than a construction error;
`api.deps.require_content_length`/`core.errors.LengthRequiredError` do not exist yet, so the two
unit-level import names fail at collection with `ImportError`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from api.deps import require_content_length
from api.factory import create_app
from core.config import Settings
from core.errors import LengthRequiredError
from tests.helpers import TEST_SECRET, fixture_body, signed_headers

_CAP_BYTES = 4096


@dataclass
class FakeEnqueue:
    """The fake of the external queue seam (`api.deps.EnqueueFn`) — records every call, never our
    own code. A local copy of `tests/test_ingest.py::FakeEnqueue`'s minimal shape (tests.md: never
    imported across test files).
    """

    calls: list[uuid.UUID] = field(default_factory=list)

    async def __call__(self, alert_id: uuid.UUID) -> None:
        self.calls.append(alert_id)


def _capped_settings() -> Settings:
    """`ingest_max_body_bytes=4096` — distinguishable from the 2,000,000 default (rule 7)."""
    return Settings(ingest_hmac_secret=SecretStr(TEST_SECRET), ingest_max_body_bytes=_CAP_BYTES)


async def test_oversized_declared_length_is_413_before_signature() -> None:
    """Interfaces `require_content_length`: a `Content-Length` over the cap is `413` for both an
    unsigned AND a correctly-signed request — the guard runs before `require_signature`, so a
    valid signature never rescues an over-cap body — and nothing is enqueued.
    """
    settings = _capped_settings()
    fake_enqueue = FakeEnqueue()
    app = create_app(settings=settings, enqueue=fake_enqueue)
    body = b"x" * (_CAP_BYTES + 1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unsigned = await client.post(
            "/api/v1/alerts", content=body, headers={"content-type": "application/json"}
        )
        signed = await client.post(
            "/api/v1/alerts", content=body, headers=signed_headers(TEST_SECRET, body)
        )

    for response in (unsigned, signed):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
    assert fake_enqueue.calls == []


async def test_body_at_cap_reaches_signature_check() -> None:
    """Interfaces `require_content_length`: a body of EXACTLY the cap size passes the guard — the
    comparison is strictly-greater-than, not greater-or-equal — reaching (and failing, unsigned)
    the signature check as `401`, never `413`. DB-less.
    """
    settings = _capped_settings()
    app = create_app(settings=settings, enqueue=FakeEnqueue())
    body = b"x" * _CAP_BYTES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_missing_content_length_is_411() -> None:
    """Interfaces `require_content_length`: a chunked request (an async-generator body makes
    `httpx` send `transfer-encoding: chunked` with NO `Content-Length` under `ASGITransport`,
    verified at briefing 2026-09-11 — a sync iterator raises `RuntimeError` instead) is refused
    `411` before the signature is ever checked.
    """
    settings = _capped_settings()
    app = create_app(settings=settings, enqueue=FakeEnqueue())

    async def body() -> AsyncIterator[bytes]:
        yield b"{}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=body(), headers={"content-type": "application/json"}
        )

    assert response.status_code == 411
    assert response.json()["error"]["code"] == "length_required"


def test_require_content_length_rejects_non_numeric_header() -> None:
    """Interfaces `require_content_length`: a non-digit `Content-Length` header raises
    `LengthRequiredError` — and the function never touches `receive` (never reads a body byte),
    pinned by a `receive` that raises if ever called.
    """
    settings = _capped_settings()

    async def receive() -> Mapping[str, object]:
        raise AssertionError("require_content_length must never read the body")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/alerts",
        "headers": [(b"content-length", b"abc")],
        "app": None,
    }
    request = Request(scope, receive)

    with pytest.raises(LengthRequiredError):
        require_content_length(request, settings)


async def test_unsigned_get_routes_need_no_content_length() -> None:
    """Interfaces: the body-cap guard lives on `SignedRoute` only — an unsigned `GET /healthz`
    (DB-less) answers its own `503 degraded`, never `411`, even with no `Content-Length` header at
    all (a GET request naturally carries none).
    """
    settings = _capped_settings()
    app = create_app(settings=settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


async def test_signed_post_under_cap_is_accepted(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Interfaces: a real, correctly-signed ingest body under the cap
    (`fixtures/alerts/alert4.json`, well below 4096 bytes) is accepted normally — the guard never
    interferes with the ordinary ingest path.
    """
    settings = _capped_settings()
    body = fixture_body("alert4")
    assert len(body) < _CAP_BYTES
    app = create_app(session_factory=db_session_factory, settings=settings, enqueue=FakeEnqueue())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=body, headers=signed_headers(TEST_SECRET, body)
        )

    assert response.status_code == 202
