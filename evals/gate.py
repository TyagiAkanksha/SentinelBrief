"""The PRD §7.4 nightly CI gate: a pure comparison of a run's `RunMetrics` against a committed
`Baseline` (m7 task-05).

`Baseline` is written ONLY by `evals.run --write-baseline` from a real v2 run — never invented
(`.claude/rules/evals.md`; PRD §7.4: "Do not invent thresholds before a baseline exists").
`evaluate_gate` checks exactly the three PRD §7.4 conditions, each parameterized by a `Settings`
field (CONVENTIONS.md §7 — never a hardcoded threshold): a severity exact-match drop of more than
`Settings.eval_gate_severity_drop_points` points below the baseline's; `critical_recall` below
`Settings.eval_gate_critical_recall_min`; and `cost_mean_usd` more than
`Settings.eval_gate_cost_rise_fraction` above the baseline's — but ONLY while the run's
`model_config` equals the baseline's (a config change disables the cost condition by design,
PRD §7.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.config import Settings
from evals.publish import metrics_from_payload, metrics_payload
from evals.scoring import RunMetrics


@dataclass(frozen=True)
class Baseline:
    """A recorded eval run to gate future runs against (PRD §7.4)."""

    recorded_at: datetime
    git_sha: str
    prompt_version: str
    model_config: dict[str, Any]
    metrics: RunMetrics


def load_baseline(path: Path) -> Baseline:
    """Load a `Baseline` from `path` (PRD §7.4: the gate never invents one).

    Args:
        path: The committed baseline JSON file's path.

    Returns:
        The recorded `Baseline`.

    Raises:
        ValueError: `path` does not exist, is not readable, or is not a valid baseline JSON.
    """
    try:
        raw = path.read_text()
    except OSError as e:
        raise ValueError(f"could not read baseline at {path}: {e}") from e
    try:
        payload = json.loads(raw)
        return Baseline(
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
            git_sha=payload["git_sha"],
            prompt_version=payload["prompt_version"],
            model_config=payload["model_config"],
            metrics=metrics_from_payload(payload["metrics"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f"invalid baseline at {path}: {e}") from e


def write_baseline(
    path: Path,
    *,
    metrics: RunMetrics,
    git_sha: str,
    prompt_version: str,
    model_config: dict[str, Any],
    now: datetime,
) -> None:
    """Write a `Baseline` to `path` (only `evals.run --write-baseline` calls this).

    Args:
        path: Where to write the baseline JSON.
        metrics: The recorded run's `RunMetrics`.
        git_sha: The git SHA the recording run was executed at.
        prompt_version: The prompt version the recording run scored.
        model_config: The recording run's effective model configuration
            (`evals.run.run_model_config`).
        now: The timestamp to record as `recorded_at`.
    """
    payload = {
        "recorded_at": now.isoformat(),
        "git_sha": git_sha,
        "prompt_version": prompt_version,
        "model_config": model_config,
        "metrics": metrics_payload(metrics),
    }
    path.write_text(json.dumps(payload, indent=2))


@dataclass(frozen=True)
class GateResult:
    """The outcome of `evaluate_gate`: every tripped PRD §7.4 condition, with details."""

    tripped: tuple[str, ...]
    details: dict[str, str]


def evaluate_gate(
    metrics: RunMetrics,
    baseline: Baseline,
    *,
    run_model_config: dict[str, Any],
    settings: Settings,
) -> GateResult:
    """Check `metrics` against `baseline` under the three PRD §7.4 conditions, nothing else.

    Args:
        metrics: The run's aggregated metrics being gated.
        baseline: The committed baseline to compare against.
        run_model_config: The run's own effective model configuration
            (`evals.run.run_model_config`); compared to `baseline.model_config` for the cost
            condition only.
        settings: The config surface carrying the three gate thresholds.

    Returns:
        A `GateResult` naming every tripped condition, in a deterministic order, with one
        `"baseline=… run=… limit=…"` detail line per tripped condition.
    """
    tripped: list[str] = []
    details: dict[str, str] = {}

    severity_limit = baseline.metrics.severity_exact - settings.eval_gate_severity_drop_points / 100
    if metrics.severity_exact < severity_limit:
        tripped.append("severity_exact_drop")
        details["severity_exact_drop"] = (
            f"baseline={baseline.metrics.severity_exact} run={metrics.severity_exact} "
            f"limit={severity_limit}"
        )

    if metrics.critical_recall < settings.eval_gate_critical_recall_min:
        tripped.append("critical_recall")
        details["critical_recall"] = (
            f"baseline={baseline.metrics.critical_recall} run={metrics.critical_recall} "
            f"limit={settings.eval_gate_critical_recall_min}"
        )

    cost_limit = baseline.metrics.cost_mean_usd * (
        Decimal(1) + Decimal(str(settings.eval_gate_cost_rise_fraction))
    )
    if run_model_config == baseline.model_config and metrics.cost_mean_usd > cost_limit:
        tripped.append("cost_rise")
        details["cost_rise"] = (
            f"baseline={baseline.metrics.cost_mean_usd} run={metrics.cost_mean_usd} "
            f"limit={cost_limit}"
        )

    return GateResult(tripped=tuple(tripped), details=details)
