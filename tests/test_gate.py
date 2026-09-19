"""Pins `evals.gate`: `Baseline`/`load_baseline`/`write_baseline`, `GateResult`/`evaluate_gate`
(PRD §7.4, m7 task-05), and `evals.publish.metrics_from_payload` (ruling R43 — the exact inverse
of `evals.publish.metrics_payload`, added in this task so `load_baseline` can rebuild a
`RunMetrics` from JSON).

The gate is EXACTLY the three PRD §7.4 conditions, nothing else: `severity_exact` more than
`Settings.eval_gate_severity_drop_points` (3) points below the baseline's; `critical_recall`
below `Settings.eval_gate_critical_recall_min` (0.90); `cost_mean_usd` above the baseline's ×
`(1 + Settings.eval_gate_cost_rise_fraction)` (0.50) while the run's `model_config` equals the
baseline's (a config change disables the cost condition by design, PRD §7.4).

`evals.gate` does not exist yet, so every test in this module is RED at collection with
`ModuleNotFoundError: No module named 'evals.gate'`, not merely at first use.

`_label`/`_verdict`/`_result` are local, minimal factories (deliberately NOT imported from
`tests/test_scoring.py`/`tests/test_eval_runs_row.py` — test files never import from each other,
per those modules' own docstrings); `_metrics`/`_baseline` build gate inputs directly rather than
through `evals.scoring.score`, since only three of `RunMetrics`' twenty-odd fields matter to the
gate (`evaluate_gate`'s own Interfaces line: it reads `severity_exact`, `critical_recall`,
`cost_mean_usd` only) — every other field is a fixed, gate-irrelevant placeholder.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.config import Settings
from core.schemas.verdict import Verdict, VerdictCategory
from evals.gate import Baseline, evaluate_gate, load_baseline, write_baseline
from evals.golden import GoldenLabel
from evals.judge import JudgeScore
from evals.publish import metrics_from_payload, metrics_payload
from evals.scoring import PR, CaseResult, RunMetrics, score

_case_id_counter = itertools.count(1)

_MODEL_CONFIG: dict[str, Any] = {
    "model": "gpt-4o-mini",
    "judge_model": "gpt-5.4",
    "replay_strict": True,
    "judge": True,
    "strong_model": "gpt-5.4",
    "prompt_version": "triage-v4",
}
_OTHER_MODEL_CONFIG: dict[str, Any] = {**_MODEL_CONFIG, "prompt_version": "triage-v5"}

_PLACEHOLDER_PR = PR(precision=0.0, recall=0.0, support=0)


def _metrics(
    *, severity_exact: float, critical_recall: float, cost_mean_usd: Decimal
) -> RunMetrics:
    """A `RunMetrics` with only the three gate-relevant fields meaningfully set; every other
    field is a fixed placeholder `evaluate_gate` never reads."""
    return RunMetrics(
        n_cases=10,
        n_failed=0,
        severity_exact=severity_exact,
        severity_within_one=severity_exact,
        category_accuracy=1.0,
        escalate_precision=1.0,
        escalate_recall=1.0,
        critical_recall=critical_recall,
        escalation_rate=0.0,
        cost_mean_usd=cost_mean_usd,
        cost_p95_usd=cost_mean_usd,
        cost_total_usd=cost_mean_usd * 10,
        latency_p50_ms=100,
        latency_p95_ms=200,
        per_severity=dict.fromkeys(range(1, 6), _PLACEHOLDER_PR),
        confusion=tuple(tuple(0 for _ in range(6)) for _ in range(5)),
        category_confusion={},
        sev_macro_f1=1.0,
    )


def _baseline(
    *,
    severity_exact: float,
    critical_recall: float,
    cost_mean_usd: Decimal,
    model_config: dict[str, Any] | None = None,
) -> Baseline:
    return Baseline(
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        git_sha="baseline0",
        prompt_version="triage-v4",
        model_config=model_config if model_config is not None else dict(_MODEL_CONFIG),
        metrics=_metrics(
            severity_exact=severity_exact,
            critical_recall=critical_recall,
            cost_mean_usd=cost_mean_usd,
        ),
    )


def _label(sev: int, cat: VerdictCategory = "brute_force") -> GoldenLabel:
    """Build a `GoldenLabel`; `escalate` follows the PRD §6.6 rubric (severity >= 4 => True)."""
    return GoldenLabel(severity=sev, category=cat, escalate=sev >= 4)


def _verdict(sev: int, cat: VerdictCategory = "brute_force") -> Verdict:
    """Build a minimally-valid `Verdict`; `escalate` follows the same rubric as `_label`."""
    return Verdict(
        severity=sev,
        category=cat,
        confidence=0.9,
        reasoning="synthetic reasoning for the metrics_from_payload round-trip test.",
        recommended_action="synthetic recommended action.",
        escalate=sev >= 4,
    )


def _judge_score(score_value: int) -> JudgeScore:
    return JudgeScore(
        score=score_value,
        cites_evidence=True,
        fabrication=False,
        conclusion_follows=True,
        rationale="synthetic judge rationale for the metrics_from_payload round-trip test.",
    )


def _result(
    label: GoldenLabel,
    verdict: Verdict | None,
    *,
    error: str | None = None,
    judge: JudgeScore | None = None,
    judge_cost: str = "0",
    tags: tuple[str, ...] = (),
) -> CaseResult:
    return CaseResult(
        case_id=f"case-{next(_case_id_counter)}",
        label=label,
        verdict=verdict,
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.000100"),
        latency_ms=10,
        error=error,
        judge=judge,
        judge_cost_usd=Decimal(judge_cost),
        tags=tags,
    )


# --- Baseline: load/write round trip -------------------------------------------------------------


def test_write_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    metrics = _metrics(severity_exact=0.83, critical_recall=0.95, cost_mean_usd=Decimal("0.000234"))
    now = datetime(2026, 9, 17, 3, 23, tzinfo=UTC)

    write_baseline(
        path,
        metrics=metrics,
        git_sha="abc1234",
        prompt_version="triage-v4",
        model_config=_MODEL_CONFIG,
        now=now,
    )
    loaded = load_baseline(path)

    assert loaded == Baseline(
        recorded_at=now,
        git_sha="abc1234",
        prompt_version="triage-v4",
        model_config=_MODEL_CONFIG,
        metrics=metrics,
    )


def test_load_missing_baseline_raises(tmp_path: Path) -> None:
    """The gate never invents a baseline (Interfaces: `load_baseline` -> `ValueError` on a
    missing/invalid file, never a default)."""
    with pytest.raises(ValueError):
        load_baseline(tmp_path / "does-not-exist.json")


# --- evaluate_gate: the three PRD §7.4 conditions, nothing else -----------------------------------


def test_severity_drop_trips_at_more_than_three_points() -> None:
    baseline = _baseline(
        severity_exact=0.80, critical_recall=1.0, cost_mean_usd=Decimal("0.000100")
    )
    settings = Settings()

    exactly_three = evaluate_gate(
        _metrics(severity_exact=0.77, critical_recall=1.0, cost_mean_usd=Decimal("0.000100")),
        baseline,
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )
    assert "severity_exact_drop" not in exactly_three.tripped

    past_three = evaluate_gate(
        _metrics(severity_exact=0.769, critical_recall=1.0, cost_mean_usd=Decimal("0.000100")),
        baseline,
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )
    assert "severity_exact_drop" in past_three.tripped

    # rule 7: a narrower configured allowance trips on a smaller drop that the 3-point default
    # above would NOT have tripped on (2.5 points below the same 0.80 baseline).
    custom_settings = Settings(eval_gate_severity_drop_points=2)
    at_two_point_five = evaluate_gate(
        _metrics(severity_exact=0.775, critical_recall=1.0, cost_mean_usd=Decimal("0.000100")),
        baseline,
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=custom_settings,
    )
    assert "severity_exact_drop" in at_two_point_five.tripped


def test_critical_recall_below_min_trips() -> None:
    baseline = _baseline(severity_exact=1.0, critical_recall=1.0, cost_mean_usd=Decimal("0.000100"))
    settings = Settings()

    below_min = evaluate_gate(
        _metrics(severity_exact=1.0, critical_recall=0.89, cost_mean_usd=Decimal("0.000100")),
        baseline,
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )
    assert "critical_recall" in below_min.tripped

    at_min = evaluate_gate(
        _metrics(severity_exact=1.0, critical_recall=0.90, cost_mean_usd=Decimal("0.000100")),
        baseline,
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )
    assert "critical_recall" not in at_min.tripped


def test_cost_rise_trips_only_without_config_change() -> None:
    baseline = _baseline(
        severity_exact=1.0,
        critical_recall=1.0,
        cost_mean_usd=Decimal("0.000100"),
        model_config=_MODEL_CONFIG,
    )
    settings = Settings()

    same_config_over = evaluate_gate(
        _metrics(severity_exact=1.0, critical_recall=1.0, cost_mean_usd=Decimal("0.000151")),
        baseline,
        run_model_config=_MODEL_CONFIG,
        settings=settings,
    )
    assert "cost_rise" in same_config_over.tripped

    changed_config_over = evaluate_gate(
        _metrics(severity_exact=1.0, critical_recall=1.0, cost_mean_usd=Decimal("0.000151")),
        baseline,
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )
    assert "cost_rise" not in changed_config_over.tripped

    same_config_under = evaluate_gate(
        _metrics(severity_exact=1.0, critical_recall=1.0, cost_mean_usd=Decimal("0.000149")),
        baseline,
        run_model_config=_MODEL_CONFIG,
        settings=settings,
    )
    assert "cost_rise" not in same_config_under.tripped


def test_gate_result_lists_every_tripped_condition() -> None:
    """All three conditions tripped at once -> `GateResult.tripped` names all three exactly
    once, in a deterministic order (proven by calling `evaluate_gate` twice over the identical
    inputs and asserting the two `tripped` tuples are equal, rather than hardcoding a specific
    order the Interfaces block does not itself pin), and `.details` carries `"baseline=… run=…
    limit=…"` for every one of them (Interfaces line)."""
    baseline = _baseline(
        severity_exact=1.0,
        critical_recall=1.0,
        cost_mean_usd=Decimal("0.000100"),
        model_config=_MODEL_CONFIG,
    )
    settings = Settings()
    metrics = _metrics(severity_exact=0.0, critical_recall=0.0, cost_mean_usd=Decimal("1.000000"))

    first = evaluate_gate(metrics, baseline, run_model_config=_MODEL_CONFIG, settings=settings)
    second = evaluate_gate(metrics, baseline, run_model_config=_MODEL_CONFIG, settings=settings)

    assert set(first.tripped) == {"severity_exact_drop", "critical_recall", "cost_rise"}
    assert len(first.tripped) == 3  # each condition named exactly once
    assert first.tripped == second.tripped  # deterministic order across repeated calls
    assert set(first.details.keys()) == set(first.tripped)
    for condition in first.tripped:
        detail = first.details[condition]
        assert "baseline=" in detail
        assert "run=" in detail
        assert "limit=" in detail


# --- evals.publish.metrics_from_payload: the exact inverse of metrics_payload (ruling R43) --------


def test_metrics_from_payload_round_trips() -> None:
    """`metrics_from_payload(metrics_payload(m)) == m` for a judged `score()` result carrying a
    failed case and both a judged and an unjudged `"injection"`-tagged case (ruling R43) --
    `load_baseline` depends on this to rebuild a `RunMetrics` from the committed JSON, and
    task-06's `--from-artifact` reuses it unchanged."""
    results = [
        _result(_label(4), _verdict(4), judge=_judge_score(5)),
        _result(_label(5), None, error="llm timeout"),
        _result(_label(3), _verdict(2), judge=_judge_score(2), tags=("injection",)),
        _result(_label(2), _verdict(2), judge=None, tags=("injection",)),
    ]
    metrics = score(results)

    assert metrics_from_payload(metrics_payload(metrics)) == metrics
