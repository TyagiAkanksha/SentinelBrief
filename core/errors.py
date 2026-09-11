"""Typed exception family for SentinelBrief (CONVENTIONS.md §4).

Every application-raised error is a `SentinelBriefError` subclass carrying a stable, snake_case
`code` class attribute — the wire value `api/errors.py::register_error_handlers` maps to an HTTP
status and the PRD §8 envelope. No bare `except Exception` and no `raise Exception(...)` anywhere
in this codebase; raise one of these instead.

`StructuredOutputError` carries plain values (`raw_text`, token counts, cost, latency) rather than
an `core.llm` result type, so this module never imports `core.llm` (CONVENTIONS.md §4 / PRD §10.1
LLM-import boundary).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar


class SentinelBriefError(Exception):
    """Base of the typed exception family; every member carries a stable `code`."""

    code: ClassVar[str] = "error"

    def __init__(self, message: str) -> None:
        """Store the human-readable message; `code` is a class attribute, not per-instance.

        Args:
            message: Human-readable description of what went wrong.
        """
        super().__init__(message)


class ConfigError(SentinelBriefError):
    """Raised when configuration is present but unusable (e.g. an unpriced configured model)."""

    code = "config_error"


class LLMCallError(SentinelBriefError):
    """Raised when the underlying LLM call itself fails (network, HTTP error, timeout)."""

    code = "llm_call_failed"


class StructuredOutputError(SentinelBriefError):
    """Raised when an LLM reply fails to parse/validate as the expected structured output.

    Carries plain values only (PRD §6.5) so `core.errors` never needs to import `core.llm`.
    """

    code = "structured_output"

    def __init__(
        self,
        message: str,
        *,
        raw_text: str,
        validation_error: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        latency_ms: int,
    ) -> None:
        """Record the failed reply plus enough trace data to bill and debug it.

        Args:
            message: Human-readable description of the failure.
            raw_text: The raw LLM reply text that failed to validate.
            validation_error: The validation error message produced by the schema.
            input_tokens: Prompt tokens consumed by the failed call.
            output_tokens: Completion tokens consumed by the failed call.
            cost_usd: Cost in USD of the failed call.
            latency_ms: Wall-clock latency in milliseconds of the failed call.
        """
        super().__init__(message)
        self.raw_text = raw_text
        self.validation_error = validation_error
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms


class VerdictValidationError(SentinelBriefError):
    """Raised when structured-output validation still fails after the one PRD §6.5 retry."""

    code = "verdict_validation"

    def __init__(self, message: str, *, attempts: int, last_error: str) -> None:
        """Record how many attempts were made and what the final validation error was.

        Args:
            message: Human-readable description of the failure.
            attempts: Total number of attempts made (including the retry).
            last_error: The validation error message from the final attempt.
        """
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class SignatureError(SentinelBriefError):
    """Raised when an ingest request's HMAC signature is missing or invalid (PRD §6.1)."""

    code = "unauthorized"


class LengthRequiredError(SentinelBriefError):
    """Raised when a signed-route request carries no usable `Content-Length` (m6 task-02)."""

    code = "length_required"


class PayloadTooLargeError(SentinelBriefError):
    """Raised when a request's declared `Content-Length` exceeds `Settings.ingest_max_body_bytes`
    (m6 task-02)."""

    code = "payload_too_large"


class NotFoundError(SentinelBriefError):
    """Raised when a requested resource (e.g. an alert) does not exist."""

    code = "not_found"


class ConflictError(SentinelBriefError):
    """Raised when a write conflicts with an existing resource (e.g. a duplicate fingerprint)."""

    code = "conflict"


class RateLimitedError(SentinelBriefError):
    """Raised when a caller exceeds a configured rate limit."""

    code = "rate_limited"


class QueueUnavailableError(SentinelBriefError):
    """Raised when the ARQ triage queue (Redis) cannot be reached (m5 task-01)."""

    code = "queue_unavailable"
