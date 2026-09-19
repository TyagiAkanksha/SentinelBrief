"""Pins `evals.scoring.render_confusion`/`render_category_confusion` and `evals.run --matrix`
(m7 task-04 fix-0, ruling R45: the brief's Goal/Files/Acceptance sections named `--matrix`; the
task-04 test table missed it — this file closes that gap before the review).

PRD §7.3 (per-severity precision/recall, confusion matrix, category confusion — the matrices are
NOT `evals.scoring.COLUMNS`/`format_table` cells, they print only through `--matrix` or land in
the per-run JSON via `evals.publish.metrics_payload`); `.claude/rules/evals.md` ("metrics are
pure functions in evals/scoring.py with unit tests").

Local `_label`/`_verdict`/`_result` helpers (a minimal subset of `tests/test_scoring.py`'s own
factories) and `_alert`/`_golden_case`/`_write_golden`/`_fake_model_env` helpers (mirroring
`tests/test_evals_run.py`'s own), deliberately NOT imported from either module — test files never
import from each other, per this repo's own convention (see both modules' docstrings).
"""

from __future__ import annotations

import itertools
import json
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest

from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict, VerdictCategory
from evals.golden import GoldenCase, GoldenLabel
from evals.run import main
from evals.scoring import CaseResult, render_category_confusion, render_confusion, score
from tests.fakes import FakeLLMClient

FIXTURES_DIR = Path("fixtures/alerts")

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
        reasoning="synthetic reasoning for the matrix-rendering test.",
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


# --- render_confusion / render_category_confusion: pure unit tests ------------------------------


def test_render_confusion_shape_and_cell_value() -> None:
    """Header names the 5 predicted-severity columns plus `failed`; 5 body rows, one per labeled
    severity band 1..5, each with 7 cells (row label + 6 confusion columns). Hand-built cell:
    band 3 labeled and predicted (`exact_3`) -> row 3 reads `1` in its own column and `0`
    everywhere else — mirrors `tests/test_scoring.py::test_confusion_matrix_shape_and_failed_
    column`'s own fixture.
    """
    exact_3 = _result(_label(3), _verdict(3))
    off_2_to_4 = _result(_label(2), _verdict(4))
    failed_5 = _result(_label(5), None, error="llm timeout")

    metrics = score([exact_3, off_2_to_4, failed_5])
    table = render_confusion(metrics)
    lines = table.rstrip("\n").splitlines()

    assert lines[0] == "| labeled\\predicted | 1 | 2 | 3 | 4 | 5 | failed |"
    body = lines[2:]
    assert len(body) == 5
    for line in body:
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == 7  # row label + 5 predicted-severity columns + failed

    row3 = [c.strip() for c in body[2].strip("|").split("|")]
    assert row3 == ["3", "0", "0", "1", "0", "0", "0"]


def test_render_category_confusion_shape_and_cell_value() -> None:
    """Header names all 7 `VerdictCategory` values plus `failed`; 7 body rows, one per category,
    each with 9 cells (row label + 7 category columns + failed). Hand-built cell: `scanning`
    labeled and predicted (`match_scanning`) -> that row reads `1` in its own column.
    """
    categories = get_args(VerdictCategory)
    assert len(categories) == 7

    match_scanning = _result(_label(1, cat="scanning"), _verdict(1, cat="scanning"))
    mismatch = _result(_label(1, cat="reconnaissance"), _verdict(1, cat="other"))

    metrics = score([match_scanning, mismatch])
    table = render_category_confusion(metrics)
    lines = table.rstrip("\n").splitlines()

    header_cells = [c.strip() for c in lines[0].strip("|").split("|")]
    assert header_cells == ["labeled\\predicted", *categories, "failed"]
    body = lines[2:]
    assert len(body) == 7
    for line in body:
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == 9  # row label + 7 category columns + failed

    scanning_row_idx = categories.index("scanning")
    scanning_row = [c.strip() for c in body[scanning_row_idx].strip("|").split("|")]
    assert scanning_row[0] == "scanning"
    assert scanning_row[1 + scanning_row_idx] == "1"


# --- evals.run --matrix: CLI test ----------------------------------------------------------------

_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials, no successful login observed.",
    "recommended_action": "monitor for continued brute-force activity.",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)


@pytest.fixture(autouse=True)
def _fake_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake, zero-priced model and no real API key (mirrors `tests/test_evals_run.py`'s own
    autouse fixture, so a developer's shell can never leak a real `LLM_API_KEY` into this CLI
    test)."""
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_JSON_MODE",
        "CHEAP_MODEL",
        "STRONG_MODEL",
        "MODEL_PRICES_JSON",
        "TRIAGE_PROMPT_VERSION",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )


def _alert(name: str) -> SessionAlert:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    return SessionAlert.model_validate(payload)


def _golden_case(name: str) -> GoldenCase:
    """One structurally-valid golden case built from a fixture alert; the label content is
    irrelevant here (this test never scores accuracy, only stdout shape)."""
    return GoldenCase(
        alert=_alert(name),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic label for the --matrix CLI test, not a scored claim.",
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def test_matrix_flag_prints_matrix_sections_only_when_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--matrix` (default off, ruling R45) prints a `## Matrix (triage-v1)` heading plus the
    confusion table's own header line (which carries the `failed` column, distinct from the
    results table's unrelated `failed`-count column that always prints) on stdout; without the
    flag, neither line appears at all.
    """
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    confusion_header = "| labeled\\predicted | 1 | 2 | 3 | 4 | 5 | failed |"

    rc_with = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--matrix",
        ],
        llm=FakeLLMClient([VALID_VERDICT_JSON]),
    )
    out_with_matrix = capsys.readouterr().out

    assert rc_with == 0
    assert "## Matrix (triage-v1)" in out_with_matrix
    assert confusion_header in out_with_matrix

    rc_without = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=FakeLLMClient([VALID_VERDICT_JSON]),
    )
    out_without_matrix = capsys.readouterr().out

    assert rc_without == 0
    assert "## Matrix" not in out_without_matrix
    assert confusion_header not in out_without_matrix
