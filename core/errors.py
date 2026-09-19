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


class FixtureMissingError(ConfigError):
    """Raised by `ReplayToolRecorder(strict=True)` when a v2 eval case has no recorded fixture
    for a tool call it makes (m7 task-02, PRD §7.2/§13).

    Subclasses `ConfigError` (ruling R24) rather than `SentinelBriefError` directly: this is a
    worker/evals-only error — no route under `api/` ever raises or names it — so
    `api/errors.py::status_for`'s MRO walk resolves it to `ConfigError`'s existing 500 mapping
    without a new `STATUS_BY_ERROR` row, keeping the M2 invariant "every concrete error has a
    mapped status" true for free.
    """

    code = "fixture_missing"


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

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        """Record how many seconds until the caller may retry, when known.

        Args:
            message: Human-readable description of what went wrong.
            retry_after: Seconds until the caller may retry, or `None` when no specific delay is
                known (`api/errors.py` then omits the `Retry-After` response header).
        """
        super().__init__(message)
        self.retry_after = retry_after


class QueueUnavailableError(SentinelBriefError):
    """Raised when the ARQ triage queue (Redis) cannot be reached (m5 task-01)."""

    code = "queue_unavailable"


class BudgetExceededError(SentinelBriefError):
    """Raised when the daily token budget (`Settings.daily_token_budget`) is already exhausted,
    BEFORE an LLM call is made (PRD §10.3; m8b task-05). Retryable: `worker/jobs.py
    ::triage_alert_job` defers the job (the alert stays `pending`) instead of routing it through
    `worker/retry.py::decide_retry`'s family-blind terminal-`failed` path (controller ruling
    R-M8b-3) — a budget-exceeded try never consumes the normal retry budget.
    """

    code = "budget_exceeded"

    def __init__(self, message: str, *, tokens_today: int, budget: int) -> None:
        """Record today's counter and the configured budget at the moment the call was blocked.

        Args:
            message: Human-readable description of what went wrong.
            tokens_today: The day's token counter's value when the call was blocked.
            budget: The configured `daily_token_budget` that was reached.
        """
        super().__init__(message)
        self.tokens_today = tokens_today
        self.budget = budget


class StreamUnavailableError(SentinelBriefError):
    """The event stream's Redis seam is not wired (m8a task-01).

    An unreachable-but-wired Redis cannot surface here (review M1): by the time a subscribe or
    read fails, `GET /api/v1/stream`'s response headers are already on the wire, so the client
    sees a 200 whose body aborts instead — see the Cleanup note on
    `api.routes.stream.verdict_event_stream`.
    """

    code = "stream_unavailable"
