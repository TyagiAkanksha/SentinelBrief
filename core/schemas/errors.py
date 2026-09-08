"""`ErrorEnvelope`: the PRD §8 error shape as a model (M2 final review, plan defect 3) —
m3 task-01.

`api/errors.py::register_error_handlers` builds one of these for every mapped exception and
every `RequestValidationError`; modeling the envelope here means the shape is pinned by a type,
not re-typed by hand at each handler.
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    """The `error` object inside a PRD §8 error envelope: a stable code plus a human message."""

    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """The PRD §8 error envelope: `{"error": {"code", "message"}}` exactly."""

    error: ErrorBody
