"""Pins `evals.scoring`'s published `sev4_rec`/`sev5_rec`/`sev_macro_f1` results-table columns
against a mutation swapping precision for recall, or rendering all three cells as the same value
(m7 task-04 fix-1, review finding I2).

Ruling R34 (`docs/plans/m7-eval-hardening/task-04-full-metrics.md`) names an exact behaviour —
"`sev4_rec`/`sev5_rec` render `per_severity[4].recall` / `[5].recall`" — but the only fixture
that exercised `format_table`'s rendering of these three cells
(`tests/test_scoring.py::test_columns_gain_three_severity_columns_and_table_renders`) was
degenerate: every quantity involved (precision, recall, macro-F1, for both bands) equalled
`1.0`, so a mutation swapping `.recall` for `.precision`, or rendering `sev_macro_f1` in all
three cells, survived the entire 1009-test suite (review finding I2). This file pins an
ASYMMETRIC fixture where all five quantities involved are pairwise distinct, so both mutations
fail.

`docs/results.md` is append-only (PRD §7.5) and these three columns publish from task-06 on — a
silently swapped precision/recall or a band-4/5 mix-up would be permanently published with no
way to correct the row (`.claude/rules/evals.md`: critical recall, the sibling metric, is "the
number that matters most").

Local `_label`/`_verdict`/`_result` helpers, a minimal subset of `tests/test_scoring.py`'s own
factories, deliberately NOT imported from that module — test files never import from each other,
per this repo's own convention.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from core.schemas.verdict import Verdict, VerdictCategory
from evals.golden import GoldenLabel
from evals.scoring import COLUMNS, CaseResult, ResultRow, format_table, score

_case_id_counter = itertools.count(1)


def _label(sev: int, cat: VerdictCategory = "brute_force") -> GoldenLabel:
    """Build a `GoldenLabel`; `escalate` follows the PRD §6.6 rubric (severity >= 4 => True)."""
    return GoldenLabel(severity=sev, category=cat, escalate=sev >= 4)


def _verdict(sev: int, cat: VerdictCategory = "brute_force") -> Verdict:
    """Build a minimally-valid `Verdict`; `escalate` follows the same rubric as `_label`."""
    return Verdict(
        severity=sev,
        category=cat,
        confidence=0.9,
        reasoning="synthetic reasoning for the results-column pin.",
        recommended_action="synthetic recommended action.",
        escalate=sev >= 4,
    )


def _result(label: GoldenLabel, verdict: Verdict) -> CaseResult:
    """Build a `CaseResult` with a unique `case_id` and fixed token counts (irrelevant here)."""
    return CaseResult(
        case_id=f"case-{next(_case_id_counter)}",
        label=label,
        verdict=verdict,
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.000100"),
        latency_ms=10,
        error=None,
    )


def test_sev4_rec_sev5_rec_sev_macro_f1_are_pairwise_distinct_in_the_rendered_row() -> None:
    """m7 task-04 fix-1, finding I2: an asymmetric fixture where `sev4_rec`
    (`per_severity[4].recall`), `sev5_rec` (`per_severity[5].recall`) and `sev_macro_f1` are
    three DIFFERENT rendered values, so the assertion pins exactly which quantity goes in which
    column.

    Band 4 (support 2): one exact hit (`hit4`) plus one miss predicting band 3 (`miss4`) ->
    recall = 1/2 = 0.50. Two more cases predict band 4 without being labeled 4 (`fp4_a` labeled
    3, `fp4_b` labeled 2), so band 4's predicted count is 3 (the hit plus these two) -> precision
    = 1/3 (~0.33) — distinct from its own recall, so a `.recall` -> `.precision` mutation changes
    the rendered `sev4_rec` cell.

    Band 5 (support 2): two exact hits (`hit5_a`, `hit5_b`), nothing else predicts band 5 ->
    recall = 1.00 — distinct from band 4's 0.50, so a band-4/5 swap changes the rendered
    `sev5_rec` cell.

    `sev_macro_f1` is the mean F1 (`2PR/(P+R)`) over every band with support > 0 (bands 2, 3, 4,
    5 here — band 1 has no labeled case and is excluded): band 2 and band 3 each have one
    labeled, zero-predicted-correctly case (P=R=0 -> F1=0.0, the P+R==0 rule), band 4's F1 is
    `2*(1/3)*0.5/((1/3)+0.5) = 0.4`, band 5's is `2*1.0*1.0/(1.0+1.0) = 1.0`; the mean is
    `(0.0 + 0.0 + 0.4 + 1.0) / 4 = 0.35` — distinct from both 0.50 and 1.00, so a mutation
    rendering all three cells as `sev_macro_f1` changes `sev4_rec`/`sev5_rec` away from their
    real values too. Verified against the real `score()` output below, not just by hand.
    """
    hit4 = _result(_label(4), _verdict(4))
    miss4 = _result(_label(4), _verdict(3))
    fp4_a = _result(_label(3), _verdict(4))
    fp4_b = _result(_label(2), _verdict(4))
    hit5_a = _result(_label(5), _verdict(5))
    hit5_b = _result(_label(5), _verdict(5))

    results = [hit4, miss4, fp4_a, fp4_b, hit5_a, hit5_b]
    metrics = score(results)

    assert metrics.per_severity[4].recall == 0.5
    assert metrics.per_severity[4].precision == pytest.approx(1 / 3)
    assert metrics.per_severity[5].recall == 1.0
    hand_computed_macro_f1 = 0.35
    assert metrics.sev_macro_f1 == pytest.approx(hand_computed_macro_f1)
    assert hand_computed_macro_f1 not in (0.50, 1.00)

    row = ResultRow(prompt_version="triage-v1", model="gpt-test", metrics=metrics)
    table = format_table([row])
    body = table.rstrip("\n").splitlines()[2:]
    assert len(body) == 1
    cells = [cell.strip() for cell in body[0].strip("|").split("|")]

    idx4 = COLUMNS.index("sev4_rec")
    idx5 = COLUMNS.index("sev5_rec")
    idx_f1 = COLUMNS.index("sev_macro_f1")

    # Literal strings, not `f"{metrics.per_severity[...].recall:.2f}"` — the whole point of this
    # pin is that a `.recall` -> `.precision` swap or an all-three-render-macro-F1 mutation must
    # fail against a fixed expectation, not the same expression the implementation uses.
    assert cells[idx4] == "0.50"
    assert cells[idx5] == "1.00"
    assert cells[idx_f1] == "0.35"
