"""Pins the PRD §8 error envelope produced once by `api/errors.py::register_error_handlers`
(CONVENTIONS.md §4) — m2 task-02.

Every `SentinelBriefError` subclass maps to its `STATUS_BY_ERROR` status with
`{"error": {"code", "message"}}`; `RequestValidationError` becomes a `422` with location and
message only, never the echoed input; any other exception becomes a generic `500` that never
leaks its message. Probe routes are mounted on a fresh `create_app()` instance per test so the
handlers are exercised the way a real route would trigger them, never by calling them directly.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import BaseModel

from api.factory import create_app
from core.errors import (
    ConfigError,
    ConflictError,
    LLMCallError,
    NotFoundError,
    RateLimitedError,
    SignatureError,
    VerdictValidationError,
)


def _mount_raiser(app: FastAPI, path: str, operation_id: str, exc: Exception) -> None:
    """Add a GET route at `path` whose handler immediately raises `exc`."""

    async def _raise() -> None:
        raise exc

    app.add_api_route(path, _raise, methods=["GET"], operation_id=operation_id)


async def _get(app: FastAPI, path: str, *, raise_server_exceptions: bool = True) -> Response:
    """GET `path` on `app` through the real ASGI surface and return the httpx response."""
    transport = ASGITransport(app=app, raise_server_exceptions=raise_server_exceptions)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_signature_error_maps_to_401_envelope() -> None:
    app = create_app()
    _mount_raiser(app, "/_probe/signature", "probe_signature", SignatureError("bad"))

    response = await _get(app, "/_probe/signature")

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthorized", "message": "bad"}}


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (NotFoundError("no such alert"), 404, "not_found"),
        (ConflictError("duplicate fingerprint"), 409, "conflict"),
        (RateLimitedError("slow down"), 429, "rate_limited"),
    ],
)
async def test_not_found_409_429_map(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    app = create_app()
    _mount_raiser(app, "/_probe/mapped", "probe_mapped", error)

    response = await _get(app, "/_probe/mapped")

    assert response.status_code == status_code
    assert response.json() == {"error": {"code": code, "message": str(error)}}


class _ProbeBody(BaseModel):
    n: int


async def test_422_enveloped_without_input_echo() -> None:
    app = create_app()

    async def _probe(body: _ProbeBody) -> dict[str, int]:
        return {"n": body.n}

    app.add_api_route("/_probe/body", _probe, methods=["POST"], operation_id="probe_body")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/_probe/body", json={"n": "SECRET-INPUT"})

    assert response.status_code == 422
    assert "SECRET-INPUT" not in response.text
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "n" in body["error"]["message"]


async def test_unhandled_exception_500_enveloped() -> None:
    app = create_app()

    async def _boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/_probe/boom", _boom, methods=["GET"], operation_id="probe_boom")

    response = await _get(app, "/_probe/boom", raise_server_exceptions=False)

    assert response.status_code == 500
    assert "boom" not in response.text
    assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ConfigError("unpriced model"), 500),
        (LLMCallError("connection reset"), 502),
        (
            VerdictValidationError(
                "gave up after retry", attempts=2, last_error="severity: field required"
            ),
            502,
        ),
    ],
)
async def test_config_error_maps_to_500_and_llm_errors_to_502(
    error: Exception,
    status_code: int,
) -> None:
    app = create_app()
    _mount_raiser(app, "/_probe/tiered", "probe_tiered", error)

    response = await _get(app, "/_probe/tiered")

    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == error.code
    assert body["error"]["message"] == str(error)
