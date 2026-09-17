"""Pins `evals.publish`: `metrics_payload`'s JSON-safe view of the new PRD §7.3 fields, and
`write_eval_run_row`'s one-row `eval_runs` insert (m7 task-04, ruling R35).

PRD §7.2 ("writes an `eval_runs` row"), §7.3 (per-severity precision/recall, confusion, category
confusion and macro-F1 all published on every row); CONVENTIONS.md §3 (services `flush()`, never
`commit()` -- the transaction boundary belongs to the caller).

`evals.publish` does not exist yet, so every test in this module is RED at collection with
`ModuleNotFoundError: No module named 'evals.publish'`, not merely at first use.

Local `_label`/`_verdict`/`_result` helpers, deliberately NOT imported from `tests/test_scoring.py`
(test files never import from each other, per that module's and `tests/test_evals_run.py`'s own
docstrings) -- a minimal subset of what those factories build, just enough to drive `score()`.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.eval_runs import EvalRunRow
from core.schemas.verdict import Verdict, VerdictCategory
from evals.golden import GoldenLabel
from evals.publish import metrics_payload, write_eval_run_row
from evals.scoring import CaseResult, score

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
        reasoning="synthetic reasoning for the eval_runs row test.",
        recommended_action="synthetic recommended action.",
        escalate=sev >= 4,
    )


def _result(label: GoldenLabel, verdict: Verdict | None, *, error: str | None = None) -> CaseResult:
    """Build a `CaseResult` with a unique `case_id` and fixed token counts (irrelevant here)."""
    return CaseResult(
        case_id=f"case-{next(_case_id_counter)}",
        label=label,
        verdict=verdict,
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.000100"),
        latency_ms=10,
        error=error,
    )


def test_metrics_payload_round_trips_new_fields() -> None:
    """`metrics_payload`'s JSON-safe view of the four new PRD §7.3 fields (ruling R35):
    `per_severity`'s keys are STRINGS (`"1".."5"` -- JSON has no int keys), `confusion` is a list
    of 5 lists of 6 ints, `category_confusion` is a dict of dicts, `sev_macro_f1` is a plain
    float -- proven by a full `json.dumps`/`json.loads` round trip, not merely by inspecting the
    payload dict directly, so a shape that only *looks* JSON-safe (e.g. int keys that happen to
    stringify under `dict` but not under `json.dumps`) cannot pass silently.
    """
    results = [
        _result(_label(4), _verdict(4)),
        _result(_label(5), None, error="llm timeout"),
    ]
    metrics = score(results)

    round_tripped = json.loads(json.dumps(metrics_payload(metrics)))

    assert set(round_tripped["per_severity"].keys()) == {"1", "2", "3", "4", "5"}
    assert round_tripped["per_severity"]["4"]["recall"] == pytest.approx(1.0)
    assert round_tripped["per_severity"]["4"]["support"] == 1
    assert isinstance(round_tripped["confusion"], list)
    assert len(round_tripped["confusion"]) == 5
    assert all(isinstance(row, list) and len(row) == 6 for row in round_tripped["confusion"])
    assert isinstance(round_tripped["category_confusion"], dict)
    assert isinstance(round_tripped["sev_macro_f1"], float)
    assert round_tripped["cost_total_usd"] == str(metrics.cost_total_usd)


async def test_write_eval_run_row_persists_metrics_json(db_session: AsyncSession) -> None:
    """`write_eval_run_row` (ruling R35) inserts exactly ONE `EvalRunRow` whose `metrics` JSONB
    column round-trips `metrics_payload(metrics)` -- `per_severity` band 4's recall reachable by
    JSON's string key, `cost_total_usd` as a `Decimal`-exact string -- and only `flush()`es
    (CONVENTIONS.md §3: the caller owns the transaction; this test commits itself afterward so its
    own read-back query can see the row).
    """
    results = [
        _result(_label(4), _verdict(4)),
        _result(_label(2), _verdict(2)),
    ]
    metrics = score(results)
    started_at = datetime(2026, 9, 17, tzinfo=UTC)
    model_config = {
        "model": "fake-model",
        "judge_model": "",
        "replay_strict": False,
        "judge": False,
    }

    row_id = await write_eval_run_row(
        db_session,
        git_sha="deadbeef",
        prompt_version="triage-v4",
        model_config=model_config,
        started_at=started_at,
        metrics=metrics,
    )
    await db_session.commit()

    count = await db_session.scalar(select(func.count()).select_from(EvalRunRow))
    assert count == 1

    row = await db_session.get(EvalRunRow, row_id)
    assert row is not None
    assert row.git_sha == "deadbeef"
    assert row.prompt_version == "triage-v4"
    assert row.model_config == model_config
    assert row.metrics is not None
    assert row.metrics["per_severity"]["4"]["recall"] == pytest.approx(1.0)
    assert row.metrics["cost_total_usd"] == str(metrics.cost_total_usd)
