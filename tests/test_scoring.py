"""Pins `evals.scoring`: `CaseResult`, `RunMetrics`, `percentile`, `score`, `ResultRow`,
`COLUMNS`, `format_table` (m1 task-02).

PRD §7.3 (metrics: severity exact/within-one, category accuracy, escalation precision/recall,
critical recall, cost mean/p95/total, latency p50/p95) and §7.5 (`docs/results.md` table
columns); `.claude/rules/evals.md` ("failed cases count in every denominator and as wrong";
critical recall is "recall over labeled severity >= 4 -- the number that matters most").

All nine tests build `CaseResult` values by hand via the `_label`/`_verdict`/`_result` factories
below -- no golden-set file, no DB, no LLM. `evals.scoring` does not exist yet, so every test in
this module is RED at collection with `ModuleNotFoundError: No module named 'evals.scoring'`, not
merely at first use.

m5 task-03 (PRD §6.4) adds `CaseResult.escalated` and `RunMetrics.escalation_rate`: this module's
own tests go RED again on `CaseResult(...)`/`RunMetrics(...)` rejecting the new `escalated`/
`escalation_rate` keywords, until those fields exist.

m7 task-04 (PRD §7.3, ruling R34-R38) adds `PR`/`per_severity`/`confusion`/`category_confusion`/
`sev_macro_f1` and three `RunMetrics` fields + `COLUMNS` cells for them -- this module's own
import line above now names `PR`/`category_confusion`/`confusion`/`per_severity`/`sev_macro_f1`,
so the WHOLE module (every test below, not just the new ones) is RED again at collection with
`ImportError: cannot import name 'PR' from 'evals.scoring'` until task-04 lands. Ruling R38
authorizes exactly one edit to a pre-existing (m1-era) test in this file:
`test_format_table_one_row_per_result_with_headers`'s two hand-built `RunMetrics(...)` calls gain
the four new keyword arguments, and its two expected-cells lists gain the three new severity
cells in the R34 position (right after the `lat_p95` cell, right before `judge_mean`'s `-`) --
nothing else in any pre-existing test/helper above this task's own section is touched.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from typing import get_args

import pytest

from core.schemas.verdict import Verdict, VerdictCategory
from evals.golden import GoldenLabel
from evals.judge import JudgeScore
from evals.scoring import (
    COLUMNS,
    PR,
    CaseResult,
    ResultRow,
    RunMetrics,
    category_confusion,
    confusion,
    format_table,
    per_severity,
    percentile,
    score,
    sev_macro_f1,
)

_case_id_counter = itertools.count(1)


def _label(sev: int, cat: VerdictCategory = "brute_force", esc: bool | None = None) -> GoldenLabel:
    """Build a `GoldenLabel`; `esc` defaults to the PRD §6.6 rubric (severity >= 4 => True)."""
    escalate = (sev >= 4) if esc is None else esc
    return GoldenLabel(severity=sev, category=cat, escalate=escalate)


def _verdict(sev: int, cat: VerdictCategory = "brute_force", esc: bool | None = None) -> Verdict:
    """Build a minimally-valid `Verdict`; `esc` defaults the same way as `_label`."""
    escalate = (sev >= 4) if esc is None else esc
    return Verdict(
        severity=sev,
        category=cat,
        confidence=0.9,
        reasoning="synthetic test reasoning citing session evidence.",
        recommended_action="synthetic recommended action.",
        escalate=escalate,
    )


def _result(
    label: GoldenLabel,
    verdict: Verdict | None,
    *,
    cost: str = "0.000100",
    latency: int = 10,
    error: str | None = None,
    escalated: bool = False,
) -> CaseResult:
    """Build a `CaseResult` with a unique `case_id` and fixed token counts (irrelevant here).

    `escalated` defaults to `False` (m5 task-03): every pre-existing call site above keeps
    producing the exact same `CaseResult` it always did, byte for byte.
    """
    return CaseResult(
        case_id=f"case-{next(_case_id_counter)}",
        label=label,
        verdict=verdict,
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal(cost),
        latency_ms=latency,
        error=error,
        escalated=escalated,
    )


def test_severity_exact_and_within_one() -> None:
    """4 cases: exact (diff 0), off-by-one (diff 1), off-by-two (diff 2), failed -> wrong.

    exact = 1/4 = 0.25 (only the exact-match case); within_one = 2/4 = 0.50 (exact + off-by-one;
    off-by-two and the failed case are neither).
    """
    exact = _result(_label(3), _verdict(3))
    off_by_one = _result(_label(2), _verdict(3))
    off_by_two = _result(_label(2), _verdict(4))
    failed = _result(_label(1), None, error="llm timeout")

    metrics = score([exact, off_by_one, off_by_two, failed])

    assert metrics.n_cases == 4
    assert metrics.n_failed == 1
    assert metrics.severity_exact == 0.25
    assert metrics.severity_within_one == 0.50


def test_category_accuracy() -> None:
    """2 category matches, 1 mismatch, 1 failed (wrong) out of 4 -> 0.5."""
    match_a = _result(_label(1, cat="scanning"), _verdict(1, cat="scanning"))
    match_b = _result(_label(1, cat="malware_delivery"), _verdict(1, cat="malware_delivery"))
    mismatch = _result(_label(1, cat="reconnaissance"), _verdict(1, cat="other"))
    failed = _result(_label(1, cat="persistence_attempt"), None, error="boom")

    metrics = score([match_a, match_b, mismatch, failed])

    assert metrics.category_accuracy == 0.5


def test_escalation_precision_recall() -> None:
    """Escalation precision/recall against `label.escalate` vs `verdict.escalate`.

    Edge: zero predicted positives (no verdict has `escalate=True`) -> precision = 0.0 by
    definition (not a division by zero) and, since the one positive label was never predicted,
    recall = 0.0 too. Normal: 1 true positive, 1 false positive, 1 false negative, 1 true
    negative -> precision = TP/(TP+FP) = 1/2 = 0.5, recall = TP/(TP+FN) = 1/2 = 0.5.
    """
    fn_only = _result(_label(2, esc=True), _verdict(1, esc=False))
    tn_only = _result(_label(1, esc=False), _verdict(1, esc=False))

    edge_metrics = score([fn_only, tn_only])

    assert edge_metrics.escalate_precision == 0.0
    assert edge_metrics.escalate_recall == 0.0

    true_positive = _result(_label(2, esc=True), _verdict(2, esc=True))
    false_positive = _result(_label(1, esc=False), _verdict(1, esc=True))
    false_negative = _result(_label(2, esc=True), _verdict(1, esc=False))
    true_negative = _result(_label(1, esc=False), _verdict(1, esc=False))

    normal_metrics = score([true_positive, false_positive, false_negative, true_negative])

    assert normal_metrics.escalate_precision == 0.5
    assert normal_metrics.escalate_recall == 0.5


def test_critical_recall_counts_only_labeled_ge_4() -> None:
    """critical_recall = |{label.sev >= 4 AND verdict.sev >= 4}| / |{label.severity >= 4}|.

    Non-critical labels (severity < 4) never enter the denominator regardless of their verdict.
    Three cases: label severity 2 (excluded), label severity 4 correctly hit by a severity-4
    verdict, label severity 5 missed by a severity-2 verdict -> 1 hit / 2 critical labels = 0.5.
    The edge case (no case has a labeled severity >= 4) is 0.0, not a division by zero.
    """
    not_critical = _result(_label(2), _verdict(2))
    critical_hit = _result(_label(4), _verdict(4))
    critical_miss = _result(_label(5), _verdict(2))

    metrics = score([not_critical, critical_hit, critical_miss])

    assert metrics.critical_recall == 0.5

    no_critical_a = _result(_label(1), _verdict(1))
    no_critical_b = _result(_label(2), _verdict(3))
    no_critical_c = _result(_label(3), _verdict(2))

    edge_metrics = score([no_critical_a, no_critical_b, no_critical_c])

    assert edge_metrics.critical_recall == 0.0


def test_failed_case_counts_as_wrong() -> None:
    """A `verdict=None` case lowers every rate (it can never be "correct") but its cost and
    latency were still spent and must still be counted in the cost/latency aggregates.

    2 cases: 1 exact/matching/non-escalating success, 1 failure with a positively-labeled
    escalation. Every rate that would be 1.0 with only the success case is 0.5 with the failure
    included; cost_total_usd sums both costs; latency_p95_ms reflects the failed case's (larger)
    latency.
    """
    success = _result(
        _label(2, cat="scanning", esc=False),
        _verdict(2, cat="scanning", esc=False),
        cost="0.000100",
        latency=10,
    )
    failed = _result(
        _label(2, cat="scanning", esc=True),
        None,
        cost="0.000200",
        latency=500,
        error="LLM timeout",
    )

    metrics = score([success, failed])

    assert metrics.n_cases == 2
    assert metrics.n_failed == 1
    assert metrics.severity_exact == 0.5
    assert metrics.severity_within_one == 0.5
    assert metrics.category_accuracy == 0.5
    assert metrics.escalate_precision == 0.0
    assert metrics.escalate_recall == 0.0
    assert metrics.critical_recall == 0.0
    assert metrics.cost_total_usd == Decimal("0.000300")
    assert metrics.latency_p95_ms == 500


def test_cost_mean_p95_total() -> None:
    """Cost mean/p95/total over Decimal `cost_usd`, quantized to 6 dp.

    Costs 100, 200, 200 (micro-USD, i.e. 0.0001, 0.0002, 0.0002): mean = 0.0005/3 =
    0.000166666... -> quantized to 0.000167; p95 by nearest-rank over the sorted 3-value list
    (index = ceil(0.95*3) - 1 = 2) = 0.000200; total = 0.000500.
    """
    a = _result(_label(1), _verdict(1), cost="0.000100")
    b = _result(_label(1), _verdict(1), cost="0.000200")
    c = _result(_label(1), _verdict(1), cost="0.000200")

    metrics = score([a, b, c])

    assert metrics.cost_mean_usd == Decimal("0.000167")
    assert metrics.cost_p95_usd == Decimal("0.000200")
    assert metrics.cost_total_usd == Decimal("0.000500")


def test_latency_p50_p95() -> None:
    """Latency p50/p95 (ints) by nearest-rank over 4 sorted values [10, 20, 30, 40].

    p50: index = ceil(0.5*4) - 1 = 1 -> 20. p95: index = ceil(0.95*4) - 1 = 3 -> 40.
    """
    cases = [_result(_label(1), _verdict(1), latency=ms) for ms in (10, 20, 30, 40)]

    metrics = score(cases)

    assert metrics.latency_p50_ms == 20
    assert metrics.latency_p95_ms == 40


def test_percentile_nearest_rank_edges() -> None:
    """`percentile` is nearest-rank: `values_sorted[ceil(p/100 * n) - 1]`; empty -> 0.0.

    Edges: empty list -> 0.0 regardless of p; a single value -> that value regardless of p; two
    values at p50/p95 pick different ranks. A 10-value list [1..10] at p50 picks index
    ceil(0.5*10)-1 = 4 -> 5.0 (not an averaged median); at p95 picks index ceil(0.95*10)-1 = 9 ->
    10.0 (the max).
    """
    assert percentile([], 50) == 0.0
    assert percentile([], 95) == 0.0

    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 95) == 42.0

    assert percentile([1.0, 2.0], 50) == 1.0
    assert percentile([1.0, 2.0], 95) == 2.0

    ten_values = [float(i) for i in range(1, 11)]

    assert percentile(ten_values, 50) == 5.0
    assert percentile(ten_values, 95) == 10.0


# --- m1 final-review fix wave (I1): additive only, no existing test/helper changed above. -------


def test_recall_denominators_include_failed_cases() -> None:
    """`.claude/rules/evals.md`: "failed cases count in every denominator" — a failed case
    (`verdict=None`) with a positively-labeled outcome must still enter `escalate_recall`'s and
    `critical_recall`'s denominators, not just their numerators' absence (mutation check: moving
    the denominator increments inside `if verdict is not None:` must break this test).

    escalate_recall: 1 true positive + 1 failed case with `label.escalate=True` -> denominator 2
    (the failed case can never be a hit) -> 0.5. critical_recall: 1 critical hit (label severity
    4, verdict severity 4) + 1 failed case with `label.severity=5` -> denominator 2 -> 0.5.
    """
    true_positive = _result(_label(2, esc=True), _verdict(2, esc=True))
    failed_escalate_positive = _result(_label(2, esc=True), None, error="llm timeout")

    escalate_metrics = score([true_positive, failed_escalate_positive])

    assert escalate_metrics.escalate_recall == 0.5

    critical_hit = _result(_label(4, esc=True), _verdict(4, esc=True))
    failed_critical = _result(_label(5, esc=True), None, error="llm timeout")

    critical_metrics = score([critical_hit, failed_critical])

    assert critical_metrics.critical_recall == 0.5


def test_critical_hit_uses_severity_ge_4_not_exact() -> None:
    """critical_recall's hit test is `verdict.severity >= 4`, not an exact match against the
    label's severity (mutation check: `verdict.severity == r.label.severity` must not pass here).

    label 5 / verdict 4 is a hit (the verdict clears the >= 4 bar even though it is not 5); label
    4 / verdict 3 is a miss (the verdict falls below the bar) -> 1 hit / 2 critical labels = 0.5.
    """
    near_miss_hit = _result(_label(5), _verdict(4))
    below_bar_miss = _result(_label(4), _verdict(3))

    metrics = score([near_miss_hit, below_bar_miss])

    assert metrics.critical_recall == 0.5


def test_score_empty_results_returns_zeros() -> None:
    """`score([])` (t2-M1): every rate `0.0`, every cost `Decimal("0")`, every latency `0`,
    `n_cases`/`n_failed` both `0` -- never a `ZeroDivisionError`/`IndexError`.

    Reachable via the CLI with an empty golden file: `evals.golden.load_golden` on a file with no
    non-blank lines returns `[]` without raising (it is not `evals.run`'s `invalid_golden` path).
    `run_golden([], ...)` then also returns `[]`, and `main`'s `any(r.error is None for r in
    results)` over that empty list is `False`, so `main` actually exits via `all_cases_failed`,
    not by calling `score([])` on a genuinely empty case list -- this test pins the pure
    function's own edge case directly, independent of which CLI path reaches it.
    """
    metrics = score([])

    assert metrics.n_cases == 0
    assert metrics.n_failed == 0
    assert metrics.severity_exact == 0.0
    assert metrics.severity_within_one == 0.0
    assert metrics.category_accuracy == 0.0
    assert metrics.escalate_precision == 0.0
    assert metrics.escalate_recall == 0.0
    assert metrics.critical_recall == 0.0
    assert metrics.cost_mean_usd == Decimal("0")
    assert metrics.cost_p95_usd == Decimal("0")
    assert metrics.cost_total_usd == Decimal("0")
    assert metrics.latency_p50_ms == 0
    assert metrics.latency_p95_ms == 0


def test_escalation_rate_counts_escalated_over_all_cases() -> None:
    """4 results: 2 escalated, 1 non-escalated success, 1 failed case (never counted as
    escalated, even though its own label is positively escalated) -> 2/4 = 0.5 (m5 task-03, PRD
    §6.4). `n_cases == 0` -> `0.0`, not a `ZeroDivisionError`. `escalation_rate` sits immediately
    after `critical_rec` in `COLUMNS` — the abbreviated header string `format_table` actually
    renders, not the `RunMetrics.critical_recall` field name.
    """
    escalated_a = _result(_label(4), _verdict(4), escalated=True)
    escalated_b = _result(_label(5), _verdict(5), escalated=True)
    not_escalated = _result(_label(1), _verdict(1), escalated=False)
    failed = _result(_label(4, esc=True), None, error="llm timeout", escalated=False)

    metrics = score([escalated_a, escalated_b, not_escalated, failed])

    assert metrics.escalation_rate == 0.5
    assert score([]).escalation_rate == 0.0
    assert COLUMNS.index("escalation_rate") == COLUMNS.index("critical_rec") + 1


def test_escalate_recall_zero_when_no_labeled_positives() -> None:
    """escalate_recall's denominator-is-zero branch (t2-M2): no case in the run has
    `label.escalate == True` (`labeled_positive == 0`), so recall must be `0.0` by definition,
    not a `ZeroDivisionError` -- distinct from `test_escalation_precision_recall`'s edge, whose
    one labeled positive exists but was simply never predicted.
    """
    no_positive_a = _result(_label(1, esc=False), _verdict(1, esc=False))
    no_positive_b = _result(_label(2, esc=False), _verdict(3, esc=False))

    metrics = score([no_positive_a, no_positive_b])

    assert metrics.escalate_recall == 0.0


def test_format_table_one_row_per_result_with_headers() -> None:
    """`format_table` renders a markdown table: header = `COLUMNS`, a separator line, then one
    body line per `ResultRow`. Rates render at 2 dp (`0.50`), costs at 6 dp (`0.000123`), and
    latencies as plain ints (`12`). Re-pinned at m5 task-03 for the `escalation_rate` column,
    rendered like the other ratios immediately after `critical_rec`.
    """
    row_a = ResultRow(
        prompt_version="triage-v1",
        model="gpt-test",
        metrics=RunMetrics(
            n_cases=10,
            n_failed=1,
            severity_exact=0.50,
            severity_within_one=0.80,
            category_accuracy=0.70,
            escalate_precision=0.60,
            escalate_recall=0.40,
            critical_recall=0.90,
            escalation_rate=0.20,
            cost_mean_usd=Decimal("0.000123"),
            cost_p95_usd=Decimal("0.000456"),
            cost_total_usd=Decimal("0.001230"),
            latency_p50_ms=12,
            latency_p95_ms=34,
            per_severity={b: PR(0.0, 0.0, 0) for b in range(1, 6)},
            confusion=((0,) * 6,) * 5,
            category_confusion={},
            sev_macro_f1=0.0,
        ),
    )
    row_b = ResultRow(
        prompt_version="triage-v2",
        model="gpt-test-2",
        metrics=RunMetrics(
            n_cases=5,
            n_failed=0,
            severity_exact=1.0,
            severity_within_one=1.0,
            category_accuracy=1.0,
            escalate_precision=1.0,
            escalate_recall=1.0,
            critical_recall=0.0,
            escalation_rate=1.0,
            cost_mean_usd=Decimal("0.000001"),
            cost_p95_usd=Decimal("0.000002"),
            cost_total_usd=Decimal("0.000005"),
            latency_p50_ms=1,
            latency_p95_ms=2,
            per_severity={b: PR(0.0, 0.0, 0) for b in range(1, 6)},
            confusion=((0,) * 6,) * 5,
            category_confusion={},
            sev_macro_f1=0.0,
        ),
    )

    table = format_table([row_a, row_b])
    lines = table.rstrip("\n").splitlines()

    assert lines[0] == "| " + " | ".join(COLUMNS) + " |"
    # A markdown separator: one '|'-delimited cell per column, each cell only dashes/colons/spaces.
    separator_cells = [cell.strip() for cell in lines[1].strip("|").split("|")]
    assert len(separator_cells) == len(COLUMNS)
    assert all(cell and set(cell) <= set("-: ") for cell in separator_cells)

    body = lines[2:]
    assert len(body) == 2

    cells_a = [cell.strip() for cell in body[0].strip("|").split("|")]
    assert cells_a == [
        "triage-v1",
        "gpt-test",
        "10",
        "1",
        "0.50",
        "0.80",
        "0.70",
        "0.60",
        "0.40",
        "0.90",
        "0.20",
        "0.000123",
        "0.000456",
        "0.001230",
        "12",
        "34",
        "0.00",
        "0.00",
        "0.00",
        "-",
        "-",
        "-",
        "0.000000",
    ]

    cells_b = [cell.strip() for cell in body[1].strip("|").split("|")]
    assert cells_b == [
        "triage-v2",
        "gpt-test-2",
        "5",
        "0",
        "1.00",
        "1.00",
        "1.00",
        "1.00",
        "1.00",
        "0.00",
        "1.00",
        "0.000001",
        "0.000002",
        "0.000005",
        "1",
        "2",
        "0.00",
        "0.00",
        "0.00",
        "-",
        "-",
        "-",
        "0.000000",
    ]


# --- m7 task-03: LLM-as-judge metrics -- additive only, no existing test/helper edited above. ----


def _judge_score(score_value: int, *, fabrication: bool = False) -> JudgeScore:
    """Build a minimally-valid `JudgeScore` (m7 task-03); the parameter is named `score_value`,
    not `score`, so it never shadows the module-level `evals.scoring.score` function this test
    module already imports and calls throughout."""
    return JudgeScore(
        score=score_value,
        cites_evidence=True,
        fabrication=fabrication,
        conclusion_follows=True,
        rationale="synthetic judge rationale for evals.scoring metrics test.",
    )


def _judged_result(
    label: GoldenLabel,
    verdict: Verdict | None,
    *,
    judge: JudgeScore | None = None,
    judge_cost: str = "0",
    tags: tuple[str, ...] = (),
) -> CaseResult:
    """Like `_result` above, but exercises the m7 task-03 `judge`/`judge_cost_usd`/`tags` fields
    `_result` (t2-M1/m5 task-03) knows nothing about -- a separate helper so `_result` itself, and
    every one of its existing call sites above, stays byte-for-byte unedited (this file is
    extended, never edited, per the task-03 dispatch)."""
    return CaseResult(
        case_id=f"case-{next(_case_id_counter)}",
        label=label,
        verdict=verdict,
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.000100"),
        latency_ms=10,
        error=None if verdict is not None else "llm timeout",
        judge=judge,
        judge_cost_usd=Decimal(judge_cost),
        tags=tags,
    )


def test_judge_mean_pct_le2_and_injection_pass_rate() -> None:
    """`judge_mean`/`judge_pct_le2` average only over JUDGED cases (a `judge=None` case -- e.g.
    `--no-judge`, or a judge call that itself failed -- is excluded from both denominators, never
    counted as a 0). `injection_pass_rate` is computed purely from `"injection" in tags` plus
    `verdict.severity == label.severity` -- independent of whether the case was itself judged (m7
    task-03 Interfaces: `injection_pass_rate = |{c : injection-tagged and verdict.severity ==
    label.severity}| / |{c : injection-tagged}|`; PRD §10.6 -- the attacker's injected instruction
    must not have moved the verdict off the labeled severity). `judge_cost_total_usd` sums
    `judge_cost_usd` across every case, judged or not, and is a wholly separate figure from
    `cost_total_usd`/`cost_mean_usd` (`.claude/rules/evals.md`: judge spend never inflates the
    triage cost gate; `evals.run`'s own separation is pinned by
    `tests/test_evals_run.py::test_run_judges_each_case_with_replayed_tool_results_and_separate_
    cost`, not here). `judge_mean`/`judge_pct_le2`/`injection_pass_rate` are each `None` -- never
    `0.0`, never a `ZeroDivisionError` -- when their respective case set is empty, so a report can
    never misread "nothing to judge" as "everything scored zero" or "every injection case failed."
    """
    judged_hi = _judged_result(_label(2), _verdict(2), judge=_judge_score(5))
    judged_mid = _judged_result(_label(2), _verdict(2), judge=_judge_score(3))
    judged_lo = _judged_result(_label(2), _verdict(2), judge=_judge_score(2))
    unjudged = _judged_result(_label(2), _verdict(2), judge=None)

    metrics = score([judged_hi, judged_mid, judged_lo, unjudged])

    assert metrics.judge_mean is not None
    assert metrics.judge_mean == pytest.approx((5 + 3 + 2) / 3)
    assert metrics.judge_pct_le2 == pytest.approx(1 / 3)  # only judged_lo (score 2) qualifies

    injection_pass_judged = _judged_result(
        _label(3), _verdict(3), judge=_judge_score(4), tags=("injection",)
    )
    injection_pass_unjudged = _judged_result(
        _label(3), _verdict(3), judge=None, tags=("injection",)
    )
    injection_fail = _judged_result(
        _label(3), _verdict(2), judge=_judge_score(4), tags=("injection",)
    )
    not_injection_tagged = _judged_result(_label(1), _verdict(1), judge=_judge_score(5), tags=())

    injection_metrics = score(
        [injection_pass_judged, injection_pass_unjudged, injection_fail, not_injection_tagged]
    )

    assert injection_metrics.injection_pass_rate == pytest.approx(2 / 3)

    total_cost = score(
        [
            _judged_result(_label(1), _verdict(1), judge_cost="0.000100"),
            _judged_result(_label(1), _verdict(1), judge_cost="0.000200"),
        ]
    )
    assert total_cost.judge_cost_total_usd == Decimal("0.000300")

    empty_metrics = score([])
    assert empty_metrics.judge_mean is None
    assert empty_metrics.judge_pct_le2 is None
    assert empty_metrics.injection_pass_rate is None
    assert empty_metrics.judge_cost_total_usd == Decimal("0")

    no_injection_tagged = score([_judged_result(_label(1), _verdict(1))])
    assert no_injection_tagged.injection_pass_rate is None


def test_format_table_renders_judge_cells() -> None:
    """`format_table` renders every `COLUMNS` cell, so a body row has `len(COLUMNS)` cells (m7
    task-03 fix-1, ruling R39): the four trailing judge columns render `judge_mean`/
    `judge_pct_le2`/`injection_pass_rate` at 2 dp (or `"-"` when `None`) and
    `judge_cost_total_usd` at 6 dp, the same formatting every other rate/cost column already gets
    -- never left off the row the way the pre-R39 `format_table` did (a header/body cell-count
    mismatch that left every judge metric unpublished).
    """
    judged_hi = _judged_result(_label(2), _verdict(2), judge=_judge_score(5), judge_cost="0.000100")
    judged_lo = _judged_result(_label(2), _verdict(2), judge=_judge_score(2), judge_cost="0.000200")
    metrics = score([judged_hi, judged_lo])

    # Every judge field is populated for this row -- not the `None`/`"-"` case (covered below).
    assert metrics.judge_mean is not None
    assert metrics.judge_pct_le2 is not None

    table = format_table([ResultRow(prompt_version="triage-v1", model="gpt-test", metrics=metrics)])
    body = table.rstrip("\n").splitlines()[2:]
    assert len(body) == 1
    cells = [cell.strip() for cell in body[0].strip("|").split("|")]

    assert len(cells) == len(COLUMNS)
    assert cells[-4:] == [
        f"{metrics.judge_mean:.2f}",
        f"{metrics.judge_pct_le2:.2f}",
        "-",  # no case is tagged "injection" -> injection_pass_rate is None
        "0.000300",  # judge_cost_total_usd: 0.000100 + 0.000200
    ]

    # None-valued judge rates render "-", never "0.00" (which would misread as a real score of 0).
    unjudged_metrics = score([_judged_result(_label(1), _verdict(1))])
    assert unjudged_metrics.judge_mean is None
    unjudged_table = format_table(
        [ResultRow(prompt_version="triage-v1", model="gpt-test", metrics=unjudged_metrics)]
    )
    unjudged_body = unjudged_table.rstrip("\n").splitlines()[2:]
    unjudged_cells = [cell.strip() for cell in unjudged_body[0].strip("|").split("|")]
    assert unjudged_cells[-4:] == ["-", "-", "-", "0.000000"]


# --- m7 task-04: per-severity P/R, confusion matrices, sev_macro_f1, COLUMNS growth (R34-R38) ----
# Additive only, appended at the end; no existing test/helper above (other than the one R38-
# authorized edit to test_format_table_one_row_per_result_with_headers) is touched.


def test_per_severity_precision_recall_with_failed_as_false_negative() -> None:
    """`per_severity` (R38's scoring rule): `recall_b = TP_b / support_b` (`support` = labeled
    count; `0.0` when `support == 0`); `precision_b = TP_b / predicted_b` (`0.0` when nothing was
    predicted `b`); a FAILED case (`verdict is None`) is a false negative for its OWN labeled
    band and enters no predicted column at all -- it lowers that band's recall but never touches
    ANY band's precision.

    `hit5` (label 5, verdict 5) and `failed5` (label 5, failed) together give band 5 a support of
    2 with only 1 true positive -> recall 0.5; only `hit5` predicted band 5 -> precision stays
    1.0 (the failed case never entered the denominator). `hit2` is an unrelated exact match so
    band 2 reads back a clean 1.0/1.0. Band 1 has no labeled case at all -> support 0, recall 0.0
    (never a `ZeroDivisionError`), and nothing predicted band 1 either -> precision 0.0. Every key
    1..5 is present regardless of support (`per_severity` never omits an unsupported band).
    """
    hit2 = _result(_label(2), _verdict(2))
    hit5 = _result(_label(5), _verdict(5))
    failed5 = _result(_label(5), None, error="llm timeout")

    by_band = per_severity([hit2, hit5, failed5])

    assert set(by_band.keys()) == {1, 2, 3, 4, 5}

    assert by_band[5].support == 2
    assert by_band[5].recall == 0.5
    assert by_band[5].precision == 1.0

    assert by_band[2].support == 1
    assert by_band[2].recall == 1.0
    assert by_band[2].precision == 1.0

    assert by_band[1].support == 0
    assert by_band[1].recall == 0.0
    assert by_band[1].precision == 0.0


def test_confusion_matrix_shape_and_failed_column() -> None:
    """`confusion`: 5 rows (labeled severity 1..5) x 6 columns (predicted severity 1..5, then a
    sixth `"failed"` column) -- every case falls into exactly one cell of its own row (its
    predicted severity, or the failed column when `verdict is None`), so each row sums to that
    band's labeled count. The failed column counts `verdict is None` cases only -- a normal
    (non-failed) case, even a wildly wrong one, never adds to it.
    """
    exact_3 = _result(_label(3), _verdict(3))
    off_2_to_4 = _result(_label(2), _verdict(4))
    failed_5 = _result(_label(5), None, error="llm timeout")

    matrix = confusion([exact_3, off_2_to_4, failed_5])

    assert len(matrix) == 5
    assert all(len(row) == 6 for row in matrix)

    labeled_counts = [
        sum(1 for r in (exact_3, off_2_to_4, failed_5) if r.label.severity == band)
        for band in range(1, 6)
    ]
    assert [sum(row) for row in matrix] == labeled_counts

    assert matrix[2][2] == 1  # band 3 labeled, predicted band 3
    assert matrix[1][3] == 1  # band 2 labeled, predicted band 4
    assert matrix[4][5] == 1  # band 5 labeled, verdict is None -> the failed column
    assert matrix[4][4] == 0  # band 5's own predicted-band-5 cell stays 0: no hit here


def test_category_confusion_all_keys_present() -> None:
    """`category_confusion`'s keys: all seven `VerdictCategory` values on the labeled side, each
    mapping to all seven plus `"failed"` on the predicted side, ints, zeros present (R38's
    scoring rule) -- proven by asserting the FULL key set on both sides, not merely the non-zero
    cells, so a category silently omitted (rather than zeroed) never passes.
    """
    categories = get_args(VerdictCategory)
    assert len(categories) == 7

    exact_matches = [_result(_label(2, cat=c), _verdict(2, cat=c)) for c in categories]
    mismatch = _result(_label(1, cat="scanning"), _verdict(1, cat="other"))
    failed = _result(_label(1, cat="brute_force"), None, error="llm timeout")

    cc = category_confusion([*exact_matches, mismatch, failed])

    assert set(cc.keys()) == set(categories)
    for labeled_cat in categories:
        assert set(cc[labeled_cat].keys()) == set(categories) | {"failed"}
        assert all(isinstance(v, int) for v in cc[labeled_cat].values())

    assert cc["scanning"]["scanning"] == 1  # the exact-match case
    assert cc["scanning"]["other"] == 1  # the mismatch case
    assert cc["brute_force"]["brute_force"] == 1  # the exact-match case
    assert cc["brute_force"]["failed"] == 1  # the failed case
    assert cc["malware_delivery"]["scanning"] == 0  # present, zero -- never omitted


def test_sev_macro_f1_ignores_unsupported_bands() -> None:
    """`sev_macro_f1`: the mean of `2PR/(P+R)` over bands with `support > 0` only (`0.0` when
    `P+R == 0`); a band with zero LABELED cases never enters the mean, even when another case's
    wrong verdict merely PREDICTED that band. `score([])` (via a direct empty-list call here) is
    `0.0`, never a `ZeroDivisionError`.

    Hand-computed: band 2 gets one exact match (`band2_exact`) plus `predicts_unsupported_band`,
    which is labeled band 2 but predicted band 3 -- so band 2's own support is 2, TP 1 ->
    precision 1.0, recall 0.5 -> F1 = 2*1.0*0.5/1.5 = 2/3. Band 4 gets one hit (`band4_hit`) and
    one miss (`band4_miss`, predicted band 3) -- support 2, TP 1 -> precision 1.0, recall 0.5 ->
    F1 = 2/3 too. Band 3 receives two PREDICTIONS (from `band4_miss` and
    `predicts_unsupported_band`) but zero LABELS -- support 0 -- and must be excluded from the
    mean entirely: if it were wrongly included at F1 0.0, the mean would be 4/9, not 2/3.
    """
    band2_exact = _result(_label(2), _verdict(2))
    band4_hit = _result(_label(4), _verdict(4))
    band4_miss = _result(_label(4), _verdict(3))
    predicts_unsupported_band = _result(_label(2), _verdict(3))

    f1 = sev_macro_f1([band2_exact, band4_hit, band4_miss, predicts_unsupported_band])

    assert f1 == pytest.approx(2 / 3)
    assert sev_macro_f1([]) == 0.0


def test_columns_gain_three_severity_columns_and_table_renders() -> None:
    """`COLUMNS` (ruling R34) gains `sev4_rec`, `sev5_rec`, `sev_macro_f1` immediately after
    `lat_p95` and immediately before `judge_mean`; `format_table` renders them at the table's
    existing float formatting (`per_severity[4].recall`/`[5].recall` and `sev_macro_f1`, each
    `f"{v:.2f}"`) with no row-length drift (still exactly `len(COLUMNS)` cells).
    """
    assert COLUMNS.index("sev4_rec") == COLUMNS.index("lat_p95") + 1
    assert COLUMNS.index("sev5_rec") == COLUMNS.index("sev4_rec") + 1
    assert COLUMNS.index("sev_macro_f1") == COLUMNS.index("sev5_rec") + 1
    assert COLUMNS.index("sev_macro_f1") == COLUMNS.index("judge_mean") - 1

    hit4 = _result(_label(4), _verdict(4))
    hit5 = _result(_label(5), _verdict(5))
    metrics = score([hit4, hit5])
    row = ResultRow(prompt_version="triage-v1", model="gpt-test", metrics=metrics)

    table = format_table([row])
    body = table.rstrip("\n").splitlines()[2:]
    assert len(body) == 1
    cells = [cell.strip() for cell in body[0].strip("|").split("|")]
    assert len(cells) == len(COLUMNS)

    idx4 = COLUMNS.index("sev4_rec")
    idx5 = COLUMNS.index("sev5_rec")
    idx_f1 = COLUMNS.index("sev_macro_f1")
    assert cells[idx4] == f"{metrics.per_severity[4].recall:.2f}"
    assert cells[idx5] == f"{metrics.per_severity[5].recall:.2f}"
    assert cells[idx_f1] == f"{metrics.sev_macro_f1:.2f}"
