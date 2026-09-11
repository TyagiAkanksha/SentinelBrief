"""Pins `worker/retry.py` — the pure retry-or-fail policy (PRD §6.2; m5 task-02 brief, Interfaces
block) — and the three new `Settings` fields it reads.

`backoff_seconds`/`decide_retry` do no I/O and no logging: every exponential-backoff/last-try
number the rest of the suite relies on is pinned here as an exact-value assertion, never exercised
by sleeping — `tests/test_worker_job_retry.py` runs its real deferrals in milliseconds
(`triage_job_backoff_base_s=0.01`) precisely because this file is the source of truth for the
arithmetic (m5 task-02 brief, rule 7).

The policy is family-blind on purpose (PRD §6.2's retry-budget bound multiplies by
`TRIAGE_JOB_MAX_TRIES` regardless of which exception family triggered it): `LLMCallError`,
`VerdictValidationError`, and any other `Exception` all retry identically below the last try.
`NotFoundError` is never passed to `decide_retry` — the job handles it before this policy ever
runs (`tests/test_worker_job_retry.py::test_missing_alert_is_not_retried`).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Settings
from core.errors import LLMCallError, VerdictValidationError
from worker.retry import RetryDecision, backoff_seconds, decide_retry


def test_backoff_doubles_from_base_and_caps_at_max() -> None:
    # The worked table from the Interfaces block, at the documented defaults (base=2, max=60):
    # try 1 -> 2.0, 2 -> 4.0, 3 -> 8.0, 4 -> 16.0, 5 -> 32.0, 6 -> 60.0 (64 capped), 7 -> 60.0.
    expected_at_defaults = {1: 2.0, 2: 4.0, 3: 8.0, 4: 16.0, 5: 32.0, 6: 60.0, 7: 60.0}
    for job_try, expected in expected_at_defaults.items():
        assert backoff_seconds(job_try, base_s=2.0, max_s=60.0) == expected

    # base=0 -> 0.0 always, at every one of the same try numbers.
    for job_try in expected_at_defaults:
        assert backoff_seconds(job_try, base_s=0.0, max_s=60.0) == 0.0

    with pytest.raises(ValueError):
        backoff_seconds(0, base_s=2.0, max_s=60.0)


def test_decide_retries_below_max_tries_with_backoff() -> None:
    cases: tuple[tuple[BaseException, str], ...] = (
        (LLMCallError("boom"), "llm_call_failed"),
        (VerdictValidationError("bad", attempts=2, last_error="x"), "verdict_validation"),
        (RuntimeError("boom"), "RuntimeError"),
    )
    for exc, reason in cases:
        decision = decide_retry(exc, job_try=1, max_tries=3, base_s=2.0, max_s=60.0)
        assert decision == RetryDecision("retry", 2.0, reason)

    decision_try2 = decide_retry(
        LLMCallError("boom"), job_try=2, max_tries=3, base_s=2.0, max_s=60.0
    )
    assert decision_try2.action == "retry"
    assert decision_try2.defer_s == 4.0


def test_decide_fails_on_the_last_try() -> None:
    last_try = decide_retry(RuntimeError("boom"), job_try=3, max_tries=3, base_s=2.0, max_s=60.0)
    assert last_try == RetryDecision("fail", 0.0, "RuntimeError")

    # A crash re-run past the cap (job_try > max_tries) still fails, never retries again.
    past_cap = decide_retry(RuntimeError("boom"), job_try=4, max_tries=3, base_s=2.0, max_s=60.0)
    assert past_cap.action == "fail"
    assert past_cap.defer_s == 0.0

    # max_tries=1 -> no retries at all: the very first try is already the last one.
    single_try = decide_retry(LLMCallError("boom"), job_try=1, max_tries=1, base_s=2.0, max_s=60.0)
    assert single_try.action == "fail"


def test_decide_rejects_nonpositive_counters() -> None:
    with pytest.raises(ValueError):
        decide_retry(RuntimeError("boom"), job_try=0, max_tries=3, base_s=2.0, max_s=60.0)
    with pytest.raises(ValueError):
        decide_retry(RuntimeError("boom"), job_try=1, max_tries=0, base_s=2.0, max_s=60.0)


def test_retry_job_settings_defaults_and_bounds() -> None:
    # R17: a `Settings()`-vs-`Settings()` comparison is tautological — these three literals are
    # the brief's own Interfaces-block defaults, not read back from a fresh Settings() instance.
    settings = Settings()
    assert settings.triage_job_max_tries == 3
    assert settings.triage_job_backoff_base_s == 2.0
    assert settings.triage_job_backoff_max_s == 60.0

    with pytest.raises(ValidationError):
        Settings(triage_job_max_tries=0)
    with pytest.raises(ValidationError):
        Settings(triage_job_backoff_base_s=-1)
