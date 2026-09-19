"""Regression sweep for `evals.gate.evaluate_gate`'s severity-drop boundary (whole-branch fix
wave, finding M1).

The old limit `baseline.metrics.severity_exact - settings.eval_gate_severity_drop_points / 100`
was computed in binary float; `0.03` has no exact float representation, so for some baselines
below 0.50 an exact 3.0-point drop tripped `severity_exact_drop` spuriously (CONVENTIONS.md §7
forbids that drift). The fix computes the limit in `Decimal`. This module sweeps several such
baselines at EXACTLY 3.0 points below (must NOT trip) and 3.01 points below (must trip); with the
default `eval_gate_severity_drop_points=3`.

`_metrics`/`_baseline` are local, minimal factories (test files never import from each other,
`.claude/rules/tests.md`) that set only the three gate-relevant fields; `run_model_config` is
deliberately unequal to the baseline's so the cost condition is disabled and this module isolates
the severity condition alone (mirrors `tests/test_gate.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.config import Settings
from evals.gate import Baseline, evaluate_gate
from evals.scoring import PR, RunMetrics

_MODEL_CONFIG: dict[str, Any] = {"model": "m", "prompt_version": "triage-v4"}
_OTHER_MODEL_CONFIG: dict[str, Any] = {"model": "m", "prompt_version": "triage-v5"}
_PLACEHOLDER_PR = PR(precision=0.0, recall=0.0, support=0)


def _metrics(severity_exact: float) -> RunMetrics:
    return RunMetrics(
        n_cases=10,
        n_failed=0,
        severity_exact=severity_exact,
        severity_within_one=severity_exact,
        category_accuracy=1.0,
        escalate_precision=1.0,
        escalate_recall=1.0,
        critical_recall=1.0,  # at/above the 0.90 default -> critical_recall never trips here
        escalation_rate=0.0,
        cost_mean_usd=Decimal("0.000100"),
        cost_p95_usd=Decimal("0.000100"),
        cost_total_usd=Decimal("0.001000"),
        latency_p50_ms=100,
        latency_p95_ms=200,
        per_severity=dict.fromkeys(range(1, 6), _PLACEHOLDER_PR),
        confusion=tuple(tuple(0 for _ in range(6)) for _ in range(5)),
        category_confusion={},
        sev_macro_f1=1.0,
    )


def _baseline(severity_exact: float) -> Baseline:
    return Baseline(
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        git_sha="baseline0",
        prompt_version="triage-v4",
        model_config=dict(_MODEL_CONFIG),
        metrics=_metrics(severity_exact),
    )


@pytest.mark.parametrize("baseline_severity", [0.04, 0.07, 0.35, 0.80])
def test_exactly_three_points_below_does_not_trip(baseline_severity: float) -> None:
    """A drop of EXACTLY the configured 3.0 points is at the limit, not past it: no trip."""
    settings = Settings()
    assert settings.eval_gate_severity_drop_points == 3
    run_severity = round(baseline_severity - 0.03, 4)

    result = evaluate_gate(
        _metrics(run_severity),
        _baseline(baseline_severity),
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )

    assert "severity_exact_drop" not in result.tripped


@pytest.mark.parametrize("baseline_severity", [0.04, 0.07, 0.35, 0.80])
def test_just_past_three_points_below_trips(baseline_severity: float) -> None:
    """A drop of 3.01 points is past the limit: it trips."""
    settings = Settings()
    run_severity = round(baseline_severity - 0.0301, 4)

    result = evaluate_gate(
        _metrics(run_severity),
        _baseline(baseline_severity),
        run_model_config=_OTHER_MODEL_CONFIG,
        settings=settings,
    )

    assert "severity_exact_drop" in result.tripped
