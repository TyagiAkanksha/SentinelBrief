"""Pins the two M5-review-deferred cleanups task-04 folds in: `worker.triage.Totals` (ruling R37)
and `worker.outcome.TriageOutcome.effective_model_primary` (ruling R36).

`.claude/rules/worker.md` ("tokens, cost and latency are summed across retries and both routing
tiers"); M5 final review deferrals t03-M1 (`Totals`) and t03-M4 (`effective_model_primary`).

`worker.triage.Totals` does not exist yet, but `worker.triage`/`worker.outcome` themselves are
already importable modules -- unlike `tests/test_scoring.py`'s whole-module RED, importing
`Totals` happens LOCALLY inside `test_totals_add_is_pure_and_sums` (mirrors
`tests/test_evals_run.py::test_run_requires_human_labels_for_v2_files`'s own local
`from evals.run import is_v2_golden`), so `test_run_totals_equal_pre_refactor_sums` -- a
regression pin that must pass BEFORE and AFTER the refactor -- can still be collected and run
today without a module-level ImportError taking the whole file down with it.
`TriageOutcome.effective_model_primary` is a plain attribute access, RED with `AttributeError`
at the assertion itself, no special import handling needed.

Local `_minimal_alert`/`VALID_VERDICT_JSON`/`_outcome` helpers, deliberately NOT imported from
`tests/test_triage_pipeline.py` (test files never import from each other, per that module's own
pattern) -- close copies of what that module already uses for the same purpose.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from core.errors import StructuredOutputError
from core.llm import LLMResult, LLMUsage
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict
from tests.fakes import FakeLLMClient
from worker.outcome import TriageOutcome
from worker.triage import TriagePipeline

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
            "session": "totals-test",
            "src_ip": "203.0.113.10",
            "sensor": "hp-test-01",
        },
        {
            "eventid": "cowrie.session.closed",
            "timestamp": (_BASE_TS + timedelta(seconds=5)).isoformat(),
            "session": "totals-test",
            "src_ip": "203.0.113.10",
            "sensor": "hp-test-01",
            "duration_ms": 5000,
        },
    ]
    return SessionAlert.model_validate(
        {
            "source": "cowrie",
            "session_id": "totals-test",
            "src_ip": "203.0.113.10",
            "sensor": "hp-test-01",
            "events": events,
        }
    )


def _llm_result(
    *, input_tokens: int = 100, output_tokens: int = 50, cost: str = "0.000100", latency: int = 5
) -> LLMResult[Verdict]:
    """A minimally-valid `LLMResult[Verdict]`, for driving `Totals.add` directly."""
    verdict = Verdict(
        severity=2,
        category="brute_force",
        confidence=0.8,
        reasoning="synthetic reasoning for the Totals.add test.",
        recommended_action="synthetic recommended action.",
        escalate=False,
    )
    return LLMResult(
        parsed=verdict,
        raw_text=VALID_VERDICT_JSON,
        model="fake-model",
        usage=LLMUsage(input_tokens, output_tokens),
        cost_usd=Decimal(cost),
        latency_ms=latency,
    )


def _structured_output_error(
    *, input_tokens: int = 10, output_tokens: int = 5, cost: str = "0.000010", latency: int = 2
) -> StructuredOutputError:
    """A `StructuredOutputError` carrying its own already-spent usage, for `Totals.add_error`."""
    return StructuredOutputError(
        "synthetic validation failure for the Totals.add_error test.",
        raw_text="not json",
        validation_error="synthetic validation error",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=Decimal(cost),
        latency_ms=latency,
    )


def _outcome(*, model: str, model_primary: str | None, escalated_model: bool) -> TriageOutcome:
    """A minimally-valid `TriageOutcome`, for driving `effective_model_primary` directly."""
    verdict = Verdict(
        severity=2,
        category="brute_force",
        confidence=0.8,
        reasoning="synthetic reasoning for the effective_model_primary test.",
        recommended_action="synthetic recommended action.",
        escalate=False,
    )
    return TriageOutcome(
        verdict=verdict,
        model=model,
        prompt_version="triage-v1",
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.000100"),
        latency_ms=5,
        retried=False,
        model_primary=model_primary,
        escalated_model=escalated_model,
    )


def test_totals_add_is_pure_and_sums() -> None:
    """`Totals` (ruling R37): a frozen accumulator with `add(result: LLMResult) -> Totals` and
    `add_error(err: StructuredOutputError) -> Totals`, both covered here since `add_error` is the
    same accumulation shape over a `StructuredOutputError`'s already-spent usage
    (`worker/triage.py:278-282`'s validation-failure path), not a separate one. Each call returns
    a NEW `Totals` with that call's own input/output tokens, cost and latency folded in, leaving
    every earlier instance completely unchanged -- a frozen dataclass, never mutated in place.
    `worker.triage` does not define `Totals` yet, so this test is RED with `ImportError: cannot
    import name 'Totals' from 'worker.triage'` at the local import below.
    """
    from worker.triage import Totals

    zero = Totals()
    assert zero.input_tokens == 0
    assert zero.output_tokens == 0
    assert zero.cost_usd == Decimal("0")
    assert zero.latency_ms == 0

    after_add = zero.add(
        _llm_result(input_tokens=100, output_tokens=50, cost="0.000100", latency=5)
    )

    assert after_add.input_tokens == 100
    assert after_add.output_tokens == 50
    assert after_add.cost_usd == Decimal("0.000100")
    assert after_add.latency_ms == 5
    # Purity: `zero` itself is untouched by `add`.
    assert zero.input_tokens == 0
    assert zero.cost_usd == Decimal("0")

    err = _structured_output_error(input_tokens=10, output_tokens=5, cost="0.000010", latency=2)
    after_error = after_add.add_error(err)

    assert after_error.input_tokens == 110
    assert after_error.output_tokens == 55
    assert after_error.cost_usd == Decimal("0.000110")
    assert after_error.latency_ms == 7
    # Purity again: `after_add` itself is untouched by `add_error`.
    assert after_add.input_tokens == 100
    assert after_add.cost_usd == Decimal("0.000100")


async def test_run_totals_equal_pre_refactor_sums() -> None:
    """REGRESSION PIN (R37): `TriagePipeline.run`'s summed tokens/cost/latency, asserted through
    the PUBLIC `run()` -- one call and one retried call, mirroring
    `tests/test_triage_pipeline.py::test_run_returns_outcome_with_verdict_and_metrics` (:103-106)
    and `::test_run_sums_tokens_cost_latency_across_attempts` (:157-158). This must pass BEFORE
    the `Totals` refactor lands (today's four bare locals) AND AFTER it (`_outcome` reading a
    `Totals` instead) -- proving the refactor is behaviour-preserving from the outside, not merely
    from a new internal unit test. `FakeLLMClient`'s default usage is 100/50 tokens, cost
    `0.000100`, latency 5 per call (`tests/fakes.py`).
    """
    single = FakeLLMClient([VALID_VERDICT_JSON])
    pipeline_single = TriagePipeline(llm=single, model="fake-model", prompt_version="triage-v1")
    outcome_single = await pipeline_single.run(_minimal_alert())

    assert outcome_single.input_tokens == 100
    assert outcome_single.output_tokens == 50
    assert outcome_single.cost_usd == Decimal("0.000100")
    assert outcome_single.latency_ms == 5

    retried = FakeLLMClient(["not json", VALID_VERDICT_JSON])
    pipeline_retried = TriagePipeline(llm=retried, model="fake-model", prompt_version="triage-v1")
    outcome_retried = await pipeline_retried.run(_minimal_alert())

    assert outcome_retried.input_tokens == 200
    assert outcome_retried.output_tokens == 100
    assert outcome_retried.cost_usd == Decimal("0.000200")
    assert outcome_retried.latency_ms == 10


def test_effective_model_primary_matches_the_three_former_sites() -> None:
    """`TriageOutcome.effective_model_primary` (ruling R36): `model_primary or model` -- the exact
    expression the three former call sites computed inline (`worker/triage.py:466`'s
    `persist_verdict(... model_primary=...)` argument, and `worker/triage_one.py:120`'s CLI JSON).
    Escalated: `model_primary` is the cheap tier's id, distinct from `model` (the strong tier that
    produced the FINAL verdict) -- `effective_model_primary` reads back the CHEAP id, never the
    strong one. Not escalated: `model_primary` is `None` (routing never ran) --
    `effective_model_primary` falls back to `model`, the only model this run ever used.
    `TriageOutcome` has no `effective_model_primary` property yet, so this test is RED with
    `AttributeError: 'TriageOutcome' object has no attribute 'effective_model_primary'`.
    """
    escalated = _outcome(model="strong-model", model_primary="cheap-model", escalated_model=True)
    assert escalated.effective_model_primary == "cheap-model"

    not_escalated = _outcome(model="cheap-model", model_primary=None, escalated_model=False)
    assert not_escalated.effective_model_primary == "cheap-model"
