"""Pins `worker/outcome.py`: `TriageOutcome`/`ToolCallRecord` relocated, frozen, importable by
`worker.store` without `worker.triage` (m2 final review M4-b; m4 task-06).

`worker/store.py` previously imported `TriageOutcome` only under `TYPE_CHECKING` from
`worker.triage`, so that `worker.store` and `worker.triage` never needed each other at import
time despite `persist_verdict` taking an `outcome: TriageOutcome` parameter. task-06 moves both
dataclasses to a new `worker/outcome.py` module that neither `worker.store` nor `worker.triage`
needs `TYPE_CHECKING` tricks to depend on: `worker.store` imports it directly (no cycle, since
`worker.outcome` imports nothing from either), and `worker.triage` re-exports both names so
`from worker.triage import TriageOutcome` (used throughout the M0-M3 suite, e.g.
`tests/helpers.py`) keeps working unchanged.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

import worker.outcome
import worker.store
import worker.triage
from core.schemas.verdict import Verdict

REPO_ROOT = Path(__file__).resolve().parent.parent


def _valid_verdict() -> Verdict:
    return Verdict(
        severity=2,
        category="brute_force",
        confidence=0.8,
        reasoning="ten failed logins with default credentials",
        recommended_action="monitor",
        escalate=False,
    )


def test_outcome_types_live_in_worker_outcome_and_are_frozen() -> None:
    # `worker.triage.TriageOutcome`/`worker.store.ToolCallRecord` are re-exports of the SAME
    # class objects `worker.outcome` defines — not merely equal, structurally-identical copies.
    assert worker.triage.TriageOutcome is worker.outcome.TriageOutcome
    assert worker.store.ToolCallRecord is worker.outcome.ToolCallRecord

    assert worker.outcome.TriageOutcome.__dataclass_params__.frozen is True  # type: ignore[attr-defined]
    assert worker.outcome.ToolCallRecord.__dataclass_params__.frozen is True  # type: ignore[attr-defined]

    outcome = worker.outcome.TriageOutcome(
        verdict=_valid_verdict(),
        model="fake-model",
        prompt_version="triage-v1",
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.000100"),
        latency_ms=5,
        retried=False,
    )
    # No `tool_calls=` given: the new field defaults to `()` so every existing (pre-task-06)
    # `TriageOutcome(...)` construction in the suite keeps working unchanged.
    assert outcome.tool_calls == ()

    with pytest.raises(FrozenInstanceError):
        outcome.model = "other-model"  # type: ignore[misc]

    record = worker.outcome.ToolCallRecord(
        seq=0,
        tool_name="lookup_ip_reputation",
        arguments={"ip": "203.0.113.10"},
        result={"abuse_score": 0},
        latency_ms=12,
    )
    with pytest.raises(FrozenInstanceError):
        record.seq = 1  # type: ignore[misc]


def test_worker_store_does_not_import_worker_triage() -> None:
    source = Path(worker.store.__file__).read_text()
    assert "worker.triage" not in source

    # Belt-and-suspenders, independent of this process's `sys.modules` (which may already carry
    # `worker.triage` from the import above): a fresh subprocess that imports ONLY `worker.store`
    # must never pull `worker.triage` in as a side effect.
    script = "import worker.store\nimport sys\nprint('worker.triage' in sys.modules)\n"
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
