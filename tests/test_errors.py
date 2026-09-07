"""Covers core.errors: the typed exception family's codes and constructor fields (m0 task-02).

Not a pinned test-author file; added by the implementer per CONVENTIONS.md §10 ("no test means
the task is not complete") — the pinned test-author files for this task never import
core.errors, and without this file the module reports 0% coverage against the CI gate
(CONVENTIONS.md §9: --cov-fail-under=90).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.errors import (
    ConfigError,
    LLMCallError,
    SentinelBriefError,
    StructuredOutputError,
    VerdictValidationError,
)


def test_sentinelbrief_error_base_code_and_message() -> None:
    err = SentinelBriefError("boom")

    assert err.code == "error"
    assert str(err) == "boom"


def test_config_error_code() -> None:
    with pytest.raises(ConfigError) as exc_info:
        raise ConfigError("unpriced model")

    assert exc_info.value.code == "config_error"
    assert str(exc_info.value) == "unpriced model"


def test_llm_call_error_code() -> None:
    with pytest.raises(LLMCallError) as exc_info:
        raise LLMCallError("connection reset")

    assert exc_info.value.code == "llm_call_failed"


def test_structured_output_error_carries_plain_fields() -> None:
    err = StructuredOutputError(
        "invalid json",
        raw_text="{not json}",
        validation_error="Expecting property name enclosed in double quotes",
        input_tokens=120,
        output_tokens=40,
        cost_usd=Decimal("0.000123"),
        latency_ms=842,
    )

    assert err.code == "structured_output"
    assert err.raw_text == "{not json}"
    assert err.validation_error == "Expecting property name enclosed in double quotes"
    assert err.input_tokens == 120
    assert err.output_tokens == 40
    assert err.cost_usd == Decimal("0.000123")
    assert err.latency_ms == 842


def test_verdict_validation_error_carries_attempts_and_last_error() -> None:
    err = VerdictValidationError(
        "gave up after retry",
        attempts=2,
        last_error="severity: field required",
    )

    assert err.code == "verdict_validation"
    assert err.attempts == 2
    assert err.last_error == "severity: field required"
