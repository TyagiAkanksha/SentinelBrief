"""Pure scoring metrics and markdown table formatting for golden-set eval runs (PRD §7.3, §7.5).

`score()` turns a run's `CaseResult`s into a `RunMetrics`; `format_table()` renders one or more
runs' metrics as the markdown table `docs/results.md` reuses (task-04). Every function here is a
pure, side-effect-free transform over already-computed values — no I/O, no golden-set loading, no
LLM or pipeline invocation (those live in `evals/golden/__init__.py` and `evals/run.py`).

Per `.claude/rules/evals.md`: failed cases (`CaseResult.verdict is None`) count in every rate's
denominator and are always wrong; costs and latencies aggregate over every case, failed included,
because that spend/time already happened.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from core.schemas.verdict import Verdict
from evals.golden import GoldenLabel

_SIX_DP = Decimal("0.000001")


@dataclass(frozen=True)
class CaseResult:
    """One golden-set case's pipeline outcome (PRD §7.2/§7.3): label plus what the run produced.

    `verdict` is `None` and `error` is set when the pipeline failed for this case (validation
    error, LLM error, tool error) — the case still counts in scoring (`.claude/rules/evals.md`).
    """

    case_id: str
    label: GoldenLabel
    verdict: Verdict | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    error: str | None


@dataclass(frozen=True)
class RunMetrics:
    """Aggregate metrics for one eval run over its `CaseResult`s (PRD §7.3).

    Rates (`severity_exact` … `critical_recall`) are fractions in `[0.0, 1.0]`; costs are exact
    `Decimal` USD; latencies are integer milliseconds.
    """

    n_cases: int
    n_failed: int
    severity_exact: float
    severity_within_one: float
    category_accuracy: float
    escalate_precision: float
    escalate_recall: float
    critical_recall: float
    cost_mean_usd: Decimal
    cost_p95_usd: Decimal
    cost_total_usd: Decimal
    latency_p50_ms: int
    latency_p95_ms: int


def _nearest_rank_index(n: int, p: float) -> int:
    """The nearest-rank index into a length-`n` sorted sequence for percentile `p` (PRD §7.3).

    `values_sorted[ceil(p / 100 * n) - 1]` — the definition pinned by
    `test_percentile_nearest_rank_edges`.
    """
    return math.ceil(p / 100 * n) - 1


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of `values` (PRD §7.3: latency p50/p95).

    Args:
        values: sample values, in any order.
        p: the percentile to compute, in `[0, 100]`.

    Returns:
        `sorted(values)[ceil(p / 100 * len(values)) - 1]`, or `0.0` when `values` is empty.
    """
    if not values:
        return 0.0
    values_sorted = sorted(values)
    return values_sorted[_nearest_rank_index(len(values_sorted), p)]


def score(results: Sequence[CaseResult]) -> RunMetrics:
    """Compute PRD §7.3 metrics over one run's case results.

    Every rate is a fraction of `n_cases` — a failed case (`verdict is None`) can never be
    counted correct, so it lowers every rate (`.claude/rules/evals.md`). Escalation precision is
    `0.0` when no case predicted escalation (rather than a division by zero); escalation recall
    and `critical_recall` are each `0.0` when their label-side denominator is empty. Costs and
    latencies aggregate over every case, including failed ones, because that spend/time was
    already incurred.

    Args:
        results: one `CaseResult` per golden-set case in the run.

    Returns:
        The aggregated `RunMetrics`.
    """
    n_cases = len(results)
    n_failed = sum(1 for r in results if r.verdict is None)

    severity_exact_count = 0
    severity_within_one_count = 0
    category_match_count = 0

    predicted_positive = 0
    true_positive = 0
    labeled_positive = 0

    critical_labeled = 0
    critical_hit = 0

    for r in results:
        verdict = r.verdict
        if verdict is not None:
            diff = abs(verdict.severity - r.label.severity)
            if diff == 0:
                severity_exact_count += 1
            if diff <= 1:
                severity_within_one_count += 1
            if verdict.category == r.label.category:
                category_match_count += 1
            if verdict.escalate:
                predicted_positive += 1
                if r.label.escalate:
                    true_positive += 1
        if r.label.escalate:
            labeled_positive += 1
        if r.label.severity >= 4:
            critical_labeled += 1
            if verdict is not None and verdict.severity >= 4:
                critical_hit += 1

    severity_exact = severity_exact_count / n_cases if n_cases else 0.0
    severity_within_one = severity_within_one_count / n_cases if n_cases else 0.0
    category_accuracy = category_match_count / n_cases if n_cases else 0.0
    escalate_precision = true_positive / predicted_positive if predicted_positive else 0.0
    escalate_recall = true_positive / labeled_positive if labeled_positive else 0.0
    critical_recall = critical_hit / critical_labeled if critical_labeled else 0.0

    costs = [r.cost_usd for r in results]
    cost_total_usd = sum(costs, Decimal("0"))
    cost_mean_usd = (
        (cost_total_usd / n_cases).quantize(_SIX_DP, rounding=ROUND_HALF_UP)
        if n_cases
        else Decimal("0.000000")
    )
    costs_sorted = sorted(costs)
    cost_p95_usd = (
        costs_sorted[_nearest_rank_index(len(costs_sorted), 95)].quantize(
            _SIX_DP, rounding=ROUND_HALF_UP
        )
        if costs_sorted
        else Decimal("0.000000")
    )

    latencies = [float(r.latency_ms) for r in results]
    latency_p50_ms = int(percentile(latencies, 50))
    latency_p95_ms = int(percentile(latencies, 95))

    return RunMetrics(
        n_cases=n_cases,
        n_failed=n_failed,
        severity_exact=severity_exact,
        severity_within_one=severity_within_one,
        category_accuracy=category_accuracy,
        escalate_precision=escalate_precision,
        escalate_recall=escalate_recall,
        critical_recall=critical_recall,
        cost_mean_usd=cost_mean_usd,
        cost_p95_usd=cost_p95_usd,
        cost_total_usd=cost_total_usd,
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
    )


@dataclass(frozen=True)
class ResultRow:
    """One `docs/results.md` table row: a prompt/model combination's `RunMetrics` (PRD §7.5)."""

    prompt_version: str
    model: str
    metrics: RunMetrics


COLUMNS: tuple[str, ...] = (
    "prompt",
    "model",
    "n",
    "failed",
    "sev_exact",
    "sev_±1",
    "category",
    "esc_prec",
    "esc_rec",
    "critical_rec",
    "cost_mean",
    "cost_p95",
    "cost_total",
    "lat_p50",
    "lat_p95",
)


def format_table(rows: Sequence[ResultRow]) -> str:
    """Render `rows` as the markdown table `docs/results.md` reuses (PRD §7.5).

    Header cells are `COLUMNS` verbatim; rates render at 2 decimal places, costs at 6, and
    latencies as plain integers.

    Args:
        rows: one `ResultRow` per prompt/model combination, in the order they should appear.

    Returns:
        A markdown table: a header line, a separator line, then one body line per row.
    """
    header = "| " + " | ".join(COLUMNS) + " |"
    separator = "|" + "|".join("---" for _ in COLUMNS) + "|"
    lines = [header, separator]
    for row in rows:
        m = row.metrics
        cells = [
            row.prompt_version,
            row.model,
            str(m.n_cases),
            str(m.n_failed),
            f"{m.severity_exact:.2f}",
            f"{m.severity_within_one:.2f}",
            f"{m.category_accuracy:.2f}",
            f"{m.escalate_precision:.2f}",
            f"{m.escalate_recall:.2f}",
            f"{m.critical_recall:.2f}",
            f"{m.cost_mean_usd:.6f}",
            f"{m.cost_p95_usd:.6f}",
            f"{m.cost_total_usd:.6f}",
            str(m.latency_p50_ms),
            str(m.latency_p95_ms),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
