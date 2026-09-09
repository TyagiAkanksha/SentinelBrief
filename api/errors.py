"""The PRD §8 error envelope, produced exactly once (CONVENTIONS.md §4).

`register_error_handlers` maps every `SentinelBriefError` subclass to its HTTP status by walking
the exception's MRO (`status_for`), envelopes `RequestValidationError` as a 422 that never echoes
the request body, and turns any other exception into a generic 500 whose traceback goes to the
log, never the response. Every ≥ 500 mapping hides its real message behind `GENERIC_MESSAGE` on
the wire too (M2 final review, plan defect 3) — only the log line carries the real text.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import (
    ConfigError,
    ConflictError,
    LLMCallError,
    NotFoundError,
    QueueUnavailableError,
    RateLimitedError,
    SentinelBriefError,
    SignatureError,
    StructuredOutputError,
    VerdictValidationError,
)
from core.schemas.errors import ErrorBody, ErrorEnvelope

logger = logging.getLogger(__name__)

GENERIC_MESSAGE = "internal error"

STATUS_BY_ERROR: Mapping[type[SentinelBriefError], int] = {
    SignatureError: 401,
    NotFoundError: 404,
    ConflictError: 409,
    RateLimitedError: 429,
    ConfigError: 500,
    LLMCallError: 502,
    VerdictValidationError: 502,
    StructuredOutputError: 502,
    QueueUnavailableError: 503,
}


def status_for(exc_type: type[SentinelBriefError]) -> int | None:
    """Resolve `exc_type`'s HTTP status by walking its MRO against `STATUS_BY_ERROR`.

    Args:
        exc_type: The raised exception's concrete class.

    Returns:
        The status of the first `STATUS_BY_ERROR` row matching `exc_type` or one of its
        ancestors (nearest first), or `None` when no ancestor is mapped.
    """
    for ancestor in exc_type.__mro__:
        if ancestor in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[ancestor]
    return None


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    """Build the one PRD §8 error envelope every handler in this module returns.

    Args:
        status_code: The HTTP status to respond with.
        code: The wire `error.code` value.
        message: The wire `error.message` value.

    Returns:
        A `JSONResponse` whose body is `ErrorEnvelope(error=ErrorBody(code=code,
        message=message))`, dumped to a plain dict.
    """
    envelope = ErrorEnvelope(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=envelope.model_dump())


async def _handle_sentinelbrief_error(request: Request, exc: Exception) -> JSONResponse:
    """Envelope a `SentinelBriefError` subclass at its MRO-resolved status.

    Args:
        request: The request that triggered the error (unused; required by the handler shape).
        exc: The raised `SentinelBriefError`.

    Returns:
        `{"error": {"code", "message"}}` at the resolved status code (falling back to 500 when
        `status_for` finds no mapped ancestor). ≥ 500 responses never carry the real message —
        only `GENERIC_MESSAGE`; the real text is logged at `ERROR` instead.
    """
    assert isinstance(exc, SentinelBriefError)
    status_code = status_for(type(exc)) or 500
    message = str(exc) if status_code < 500 else GENERIC_MESSAGE
    if status_code >= 500:
        logger.error("sentinelbrief error code=%s status=%s message=%s", exc.code, status_code, exc)
    return _envelope(status_code, exc.code, message)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Envelope a `RequestValidationError` as a 422 with location + message, never the input.

    Args:
        request: The request that triggered the error (unused; required by the handler shape).
        exc: The raised `RequestValidationError`.

    Returns:
        A 422 `{"error": {"code": "validation_error", "message": ...}}`.
    """
    assert isinstance(exc, RequestValidationError)
    message = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return _envelope(422, "validation_error", message)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Envelope Starlette's built-in `HTTPException` (PRD §8 "one envelope everywhere").

    FastAPI's own `HTTPException` subclasses `starlette.exceptions.HTTPException`, so
    registering on the Starlette base covers both raise sites. Without this handler an unmatched
    path (404) or a wrong-method request (405) answers Starlette's default `{"detail": ...}`
    body instead of the PRD §8 envelope (task-03 review finding I2).

    Args:
        request: The request that triggered the error (unused; required by the handler shape).
        exc: The raised `starlette.exceptions.HTTPException` (or `fastapi.HTTPException`).

    Returns:
        `{"error": {"code", "message"}}` at `exc.status_code`, with `exc.headers` copied onto
        the response when present (405 keeps its `Allow` header). ≥ 500 responses never carry
        the real detail on the wire — only `GENERIC_MESSAGE`; the real text is logged at `ERROR`.
    """
    assert isinstance(exc, StarletteHTTPException)
    status_code = exc.status_code
    code = {404: "not_found", 405: "method_not_allowed"}.get(status_code, "http_error")
    message = str(exc.detail) if status_code < 500 else GENERIC_MESSAGE
    if status_code >= 500:
        logger.error("http exception code=%s status=%s detail=%s", code, status_code, exc.detail)
    response = _envelope(status_code, code, message)
    if exc.headers:
        for key, value in exc.headers.items():
            response.headers[key] = value
    return response


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Envelope any other exception as a generic 500; the traceback goes to the log only.

    Args:
        request: The request that triggered the error (unused; required by the handler shape).
        exc: The unhandled exception.

    Returns:
        A 500 `{"error": {"code": "internal_error", "message": "internal error"}}`.
    """
    logger.exception("unhandled exception", exc_info=exc)
    return _envelope(500, "internal_error", GENERIC_MESSAGE)


def register_error_handlers(app: FastAPI) -> None:
    """Register the four error-handling tiers on `app`, exactly once.

    Args:
        app: The `FastAPI` instance to register handlers on.
    """
    app.add_exception_handler(SentinelBriefError, _handle_sentinelbrief_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unhandled_exception)
