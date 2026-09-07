"""Pins `worker.triage.TriagePipeline`: one retry on `StructuredOutputError`, none on
`LLMCallError`, tokens/cost/latency summed across attempts (m0 task-04).

PRD §6.5 (structured-output contract, retry-once), §10.6 (attacker data delimited);
CONVENTIONS.md §13 / `.claude/rules/worker.md` ("retry exactly once with the validation error
appended; then raise `VerdictValidationError(attempts=2, ...)`; `LLMCallError` is not retried
here"). Uses `tests.fakes.FakeLLMClient` — the only LLM double in the suite — never a hand-rolled
mock of our own code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.errors import LLMCallError, StructuredOutputError, VerdictValidationError
from core.llm import LLMUsage, parse_structured
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict
from tests.fakes import FakeLLMClient
from worker.triage import RETRY_INSTRUCTION, TriageOutcome, TriagePipeline

# The task brief's fixed valid-verdict example, reused across every test that needs one.
_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials",
    "recommended_action": "monitor",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)

_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _minimal_alert() -> SessionAlert:
    events: list[dict[str, Any]] = [
        {
            "eventid": "cowrie.session.connect",
            "timestamp": _BASE_TS.isoformat(),
            "session": "pipeline-test",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
        },
        {
            "eventid": "cowrie.session.closed",
            "timestamp": (_BASE_TS + timedelta(seconds=5)).isoformat(),
            "session": "pipeline-test",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
            "duration_ms": 5000,
        },
    ]
    return SessionAlert.model_validate(
        {
            "source": "cowrie",
            "session_id": "pipeline-test",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
            "events": events,
        }
    )


def _validation_error_for(raw_text: str) -> str:
    """The exact `validation_error` a `StructuredOutputError` carries for `raw_text`.

    Computed through the same `core.llm.parse_structured` path `FakeLLMClient` replays through,
    so the retry-prompt assertion below doesn't hardcode pydantic's exact wording.
    """
    with pytest.raises(StructuredOutputError) as exc_info:
        parse_structured(
            raw_text,
            Verdict,
            model="fake-model",
            usage=LLMUsage(1, 1),
            cost_usd=Decimal("0"),
            latency_ms=1,
        )
    return exc_info.value.validation_error


async def test_run_returns_outcome_with_verdict_and_metrics() -> None:
    fake = FakeLLMClient([VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    outcome = await pipeline.run(_minimal_alert())

    assert isinstance(outcome, TriageOutcome)
    assert isinstance(outcome.verdict, Verdict)
    assert outcome.verdict.category == "brute_force"
    assert outcome.retried is False
    assert outcome.input_tokens == 100
    assert outcome.output_tokens == 50
    assert outcome.cost_usd == Decimal("0.000100")
    assert outcome.latency_ms == 5
    assert len(fake.calls) == 1


async def test_run_retries_once_appending_validation_error() -> None:
    fake = FakeLLMClient(["not json", VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    outcome = await pipeline.run(_minimal_alert())

    assert outcome.retried is True
    assert len(fake.calls) == 2

    first_call_messages = fake.calls[0].messages
    assert len(first_call_messages) == 2
    assert first_call_messages[0]["role"] == "system"
    assert first_call_messages[1]["role"] == "user"

    second_call_messages = fake.calls[1].messages
    assert len(second_call_messages) == 4
    assert second_call_messages[-2]["role"] == "assistant"
    assert second_call_messages[-2]["content"] == "not json"
    assert second_call_messages[-1]["role"] == "user"

    retry_content = second_call_messages[-1]["content"]
    expected_error = _validation_error_for("not json")
    prefix, _, suffix = RETRY_INSTRUCTION.partition("{error}")
    assert "failed validation" in retry_content
    assert expected_error in retry_content
    assert retry_content.startswith(prefix)
    assert retry_content.endswith(suffix)


async def test_run_raises_verdict_validation_error_after_second_failure() -> None:
    fake = FakeLLMClient(["{}", "{}"])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    with pytest.raises(VerdictValidationError) as exc_info:
        await pipeline.run(_minimal_alert())

    assert exc_info.value.attempts == 2
    assert exc_info.value.last_error != ""
    assert len(fake.calls) == 2


async def test_run_sums_tokens_cost_latency_across_attempts() -> None:
    fake = FakeLLMClient(["not json", VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    outcome = await pipeline.run(_minimal_alert())

    assert outcome.input_tokens == 200
    assert outcome.output_tokens == 100
    assert outcome.cost_usd == Decimal("0.000200")
    assert outcome.latency_ms == 10


async def test_run_does_not_retry_llm_call_error() -> None:
    fake = FakeLLMClient([LLMCallError("boom")])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    with pytest.raises(LLMCallError):
        await pipeline.run(_minimal_alert())

    assert len(fake.calls) == 1


async def test_run_uses_configured_model_and_prompt_version() -> None:
    fake = FakeLLMClient([VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    outcome = await pipeline.run(_minimal_alert())

    assert fake.calls[0].model == "fake-model"
    assert outcome.model == "fake-model"
    assert outcome.prompt_version == "triage-v1"
