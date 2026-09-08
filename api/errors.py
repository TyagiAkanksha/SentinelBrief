"""The PRD §8 error envelope, produced exactly once (CONVENTIONS.md §4).

`register_error_handlers` maps every `SentinelBriefError` subclass to its HTTP status via
`STATUS_BY_ERROR`, envelopes `RequestValidationError` as a 422 that never echoes the request body,
and turns any other exception into a generic 500 whose traceback goes to the log, never the
response.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.errors import (
    ConfigError,
    ConflictError,
    LLMCallError,
    NotFoundError,
    RateLimitedError,
    SentinelBriefError,
    SignatureError,
    StructuredOutputError,
    VerdictValidationError,
)

logger = logging.getLogger(__name__)

STATUS_BY_ERROR: Mapping[type[SentinelBriefError], int] = {
    SignatureError: 401,
    NotFoundError: 404,
    ConflictError: 409,
    RateLimitedError: 429,
    ConfigError: 500,
    LLMCallError: 502,
    VerdictValidationError: 502,
    StructuredOutputError: 502,
}


async def _handle_sentinelbrief_error(request: Request, exc: Exception) -> JSONResponse:
    """Envelope a `SentinelBriefError` subclass at its `STATUS_BY_ERROR` status.

    Args:
        request: The request that triggered the error (unused; required by the handler shape).
        exc: The raised `SentinelBriefError`.

    Returns:
        `{"error": {"code", "message"}}` at the mapped status code.
    """
    assert isinstance(exc, SentinelBriefError)
    status_code = STATUS_BY_ERROR.get(type(exc), 500)
    return JSONResponse(
        status_code=status_code, content={"error": {"code": exc.code, "message": str(exc)}}
    )


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
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": message}},
    )


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Envelope any other exception as a generic 500; the traceback goes to the log only.

    Args:
        request: The request that triggered the error (unused; required by the handler shape).
        exc: The unhandled exception.

    Returns:
        A 500 `{"error": {"code": "internal_error", "message": "internal error"}}`.
    """
    logger.exception("unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "internal error"}},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register the three error-handling tiers on `app`, exactly once.

    Args:
        app: The `FastAPI` instance to register handlers on.
    """
    app.add_exception_handler(SentinelBriefError, _handle_sentinelbrief_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unhandled_exception)
