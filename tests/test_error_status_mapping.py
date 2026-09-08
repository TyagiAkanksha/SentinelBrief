"""Pins `api/errors.py::status_for`'s MRO walk and the ≥ 500 generic-message hardening
(M2 final review, plan defect 3) — m3 task-02.

`status_for` must resolve a status for *every* concrete `SentinelBriefError` subclass — including
one nobody has written yet, via the MRO walk — and every handler's JSON body must validate as an
`ErrorEnvelope`. The ≥ 500 tier never leaks its real message on the wire (only in the log); the
`code` field is unaffected by that hardening and always stays the raising class's own `code`.
"""

from __future__ import annotations

import inspect
import logging

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, Response
from pydantic import BaseModel

from api.errors import status_for
from api.factory import create_app
from core import errors as core_errors
from core.errors import (
    ConfigError,
    ConflictError,
    LLMCallError,
    NotFoundError,
    SentinelBriefError,
    SignatureError,
)
from core.schemas.errors import ErrorEnvelope


def _mount_raiser(app: FastAPI, path: str, operation_id: str, exc: Exception) -> None:
    """Add a GET route at `path` whose handler immediately raises `exc`."""

    async def _raise() -> None:
        raise exc

    app.add_api_route(path, _raise, methods=["GET"], operation_id=operation_id)


async def _get(app: FastAPI, path: str, *, raise_app_exceptions: bool = True) -> Response:
    """GET `path` on `app` through the real ASGI surface and return the httpx response."""
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_status_for_walks_the_mro() -> None:
    class _Gone(NotFoundError):
        pass

    assert status_for(_Gone) == 404

    app = create_app()
    _mount_raiser(app, "/_probe/gone", "probe_gone", _Gone("no longer here"))

    response = await _get(app, "/_probe/gone")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_every_concrete_error_has_a_status() -> None:
    concrete_errors = [
        obj
        for _name, obj in inspect.getmembers(core_errors, inspect.isclass)
        if issubclass(obj, SentinelBriefError) and obj is not SentinelBriefError
    ]

    assert concrete_errors, "expected at least one concrete SentinelBriefError subclass"
    for error_cls in concrete_errors:
        assert status_for(error_cls) is not None, f"{error_cls.__name__} has no mapped status"


async def test_status_for_unmapped_returns_none() -> None:
    class _Unmapped(SentinelBriefError):
        code = "unmapped_probe"

    assert status_for(_Unmapped) is None

    app = create_app()
    _mount_raiser(app, "/_probe/unmapped", "probe_unmapped", _Unmapped("boom"))

    response = await _get(app, "/_probe/unmapped")

    body = response.json()
    assert response.status_code == 500
    # No row -> `status_for` falls back to `None`, and the handler's `or 500` catches it; the
    # wire `code` still comes from the raising class, only the message is genericized.
    assert body["error"]["code"] == "unmapped_probe"
    assert body["error"]["message"] == "internal error"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ConfigError("unpriced model xyz"), 500),
        (LLMCallError("connection reset by peer"), 502),
    ],
)
async def test_5xx_mapped_errors_use_generic_message_and_log_the_real_one(
    error: SentinelBriefError,
    status_code: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()
    _mount_raiser(app, "/_probe/tiered", "probe_tiered", error)

    with caplog.at_level(logging.ERROR):
        response = await _get(app, "/_probe/tiered")

    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == error.code
    assert body["error"]["message"] == "internal error"
    assert str(error) not in response.text
    assert any(str(error) in record.message for record in caplog.records)
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


async def test_4xx_mapped_errors_keep_their_message() -> None:
    app = create_app()
    error = ConflictError("dup")
    _mount_raiser(app, "/_probe/conflict", "probe_conflict", error)

    response = await _get(app, "/_probe/conflict")

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "dup"


class _ProbeBody(BaseModel):
    n: int


async def _trigger(app: FastAPI, kind: str) -> Response:
    """Mount and trigger one of the four handler tiers on a fresh probe path."""
    if kind == "signature_error":
        _mount_raiser(app, "/_probe/e", "probe_e", SignatureError("bad"))
        return await _get(app, "/_probe/e", raise_app_exceptions=False)
    if kind == "not_found":
        _mount_raiser(app, "/_probe/e", "probe_e", NotFoundError("nope"))
        return await _get(app, "/_probe/e", raise_app_exceptions=False)
    if kind == "validation":

        async def _probe(body: _ProbeBody) -> dict[str, int]:
            return {"n": body.n}

        app.add_api_route("/_probe/e", _probe, methods=["POST"], operation_id="probe_e")
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/_probe/e", json={"n": "not-an-int"})

    async def _boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/_probe/e", _boom, methods=["GET"], operation_id="probe_e")
    return await _get(app, "/_probe/e", raise_app_exceptions=False)


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        ("signature_error", 401),
        ("not_found", 404),
        ("validation", 422),
        ("unhandled", 500),
    ],
)
async def test_every_handler_output_validates_as_error_envelope(
    kind: str, expected_status: int
) -> None:
    app = create_app()

    response = await _trigger(app, kind)

    assert response.status_code == expected_status
    body = response.json()
    envelope = ErrorEnvelope.model_validate(body)
    assert envelope.model_dump() == body


async def test_unmatched_path_404_uses_envelope() -> None:
    """I2 (m3 task-03 review): PRD §8 says "one envelope everywhere", but an unmatched path hits
    Starlette's built-in `StarletteHTTPException` today, which `register_error_handlers` does not
    map — so it answers `{"detail": "Not Found"}` instead of the PRD §8 envelope. This pins the
    fix: `not_found` / "Not Found", body keys exactly `{"error"}`.
    """
    app = create_app()

    response = await _get(app, "/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"] == {"code": "not_found", "message": "Not Found"}


async def test_method_not_allowed_405_uses_envelope() -> None:
    """I2 (m3 task-03 review): a wrong-method request must keep Starlette's `Allow` header *and*
    answer the PRD §8 envelope (`method_not_allowed` / "Method Not Allowed") instead of
    `{"detail": "Method Not Allowed"}`.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/stats")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"] == {"code": "method_not_allowed", "message": "Method Not Allowed"}


async def test_5xx_http_exception_uses_generic_message_and_logs_the_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """N1 (m3 task-03 re-review): `_handle_http_exception`'s >=500 branch shares the same
    secret-hiding invariant M2's final review forced onto `_handle_sentinelbrief_error`
    (`test_5xx_mapped_errors_use_generic_message_and_log_the_real_one` above), but had no
    regression guard of its own — mutation R5 (an unconditional `str(exc.detail)`) leaked the
    real detail on the wire while the whole suite still passed. This pins it: the real `detail`
    never reaches the response body, only the log.
    """
    app = create_app()
    _mount_raiser(
        app,
        "/_probe/http5xx",
        "probe_http5xx",
        HTTPException(status_code=503, detail="secret detail"),
    )

    with caplog.at_level(logging.ERROR):
        response = await _get(app, "/_probe/http5xx", raise_app_exceptions=False)

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "http_error", "message": "internal error"}}
    assert "secret detail" not in response.text
    assert any(
        record.name == "api.errors" and "secret detail" in record.message
        for record in caplog.records
    )
    assert any(record.levelno >= logging.ERROR for record in caplog.records)
