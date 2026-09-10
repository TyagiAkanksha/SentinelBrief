"""The pure retry-or-fail policy for `worker/jobs.py::triage_alert_job` (PRD §6.2; m5 task-02
brief, Interfaces block).

No I/O, no logging, no `arq` import: `decide_retry` only decides; the job (`worker/jobs.py`) owns
every side effect (raising `arq.worker.Retry`, writing the terminal `failed` status, logging). The
policy is family-blind on purpose — `LLMCallError` (transient transport/HTTP),
`VerdictValidationError` (a poison reply may be deterministic, but PRD §6.2's retry-budget bound
multiplies by `TRIAGE_JOB_MAX_TRIES` regardless) and any other `Exception` all retry identically
until the last try. `NotFoundError` never reaches this module — the job handles it before
`decide_retry` runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.errors import SentinelBriefError


def backoff_seconds(job_try: int, *, base_s: float, max_s: float) -> float:
    """The exponential backoff delay before retry number `job_try`, capped at `max_s`.

    Args:
        job_try: The 1-based attempt number that just failed (ARQ's own `job_try`).
        base_s: The delay before the first retry, in seconds; doubles each retry.
        max_s: The cap on the delay, in seconds.

    Returns:
        `min(base_s * 2 ** (job_try - 1), max_s)`.

    Raises:
        ValueError: `job_try < 1`.
    """
    if job_try < 1:
        raise ValueError("job_try must be >= 1")
    return min(base_s * (2.0 ** (job_try - 1)), max_s)


@dataclass(frozen=True)
class RetryDecision:
    """What `decide_retry` decided: retry (with a deferral) or fail (terminally)."""

    action: Literal["retry", "fail"]
    defer_s: float
    """0.0 when `action == "fail"`."""
    reason: str
    """`exc.code` for a `SentinelBriefError`, else `type(exc).__name__` — the string that is
    logged; never the exception's own message text (which may embed attacker-controlled data)."""


def decide_retry(
    exc: BaseException, *, job_try: int, max_tries: int, base_s: float, max_s: float
) -> RetryDecision:
    """Decide whether `exc` (raised by attempt `job_try`) should be retried or is terminal.

    Args:
        exc: The exception the job caught (never `NotFoundError` — the job handles that first).
        job_try: The 1-based attempt number that just raised.
        max_tries: The total number of attempts allowed (PRD §6.2's "× 3").
        base_s: The delay before the first retry, in seconds; doubles each retry.
        max_s: The cap on that delay, in seconds.

    Returns:
        `RetryDecision("retry", backoff_seconds(job_try, ...), reason)` when `job_try < max_tries`;
        `RetryDecision("fail", 0.0, reason)` on the last allowed try (`job_try >= max_tries`,
        including a crash re-run past the cap).

    Raises:
        ValueError: `job_try < 1` or `max_tries < 1`.
    """
    if job_try < 1:
        raise ValueError("job_try must be >= 1")
    if max_tries < 1:
        raise ValueError("max_tries must be >= 1")
    reason = exc.code if isinstance(exc, SentinelBriefError) else type(exc).__name__
    if job_try >= max_tries:
        return RetryDecision("fail", 0.0, reason)
    return RetryDecision("retry", backoff_seconds(job_try, base_s=base_s, max_s=max_s), reason)
