"""Supplementary coverage for `evals.run`'s `--gate`/`--write-baseline` happy paths (m7 task-05,
implementer-added — not test-author-pinned). `tests/test_gate.py` and `tests/test_evals_run.py`
(test-author, pinned) already cover `evals.gate`'s own unit behavior and every CLI *refusal*
path; this module adds the successful paths those don't reach: writing a brand-new baseline,
`--force-baseline` overwriting one (with the old-vs-new print), a `--gate` run that trips no
condition (`GATE: PASS`), and `evals.gate.load_baseline` rejecting invalid (non-missing) JSON.

Test files never import helpers from each other (`.claude/rules/tests.md`) — every helper below
is a small, local redefinition.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.schemas.alert import SessionAlert
from evals.gate import load_baseline, write_baseline
from evals.golden import GoldenCase, GoldenLabel
from evals.run import main
from evals.scoring import PR, RunMetrics
from tests.fakes import FakeLLMClient

FIXTURES_DIR = Path("fixtures/alerts")

_SETTINGS_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_JSON_MODE",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "ENVIRONMENT",
)


@pytest.fixture(autouse=True)
def _fake_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake, zero-priced model and no real API key for every test in this module (mirrors
    `tests/test_evals_run.py`'s own autouse fixture, redefined locally per the no-cross-import
    rule)."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}'
    )


def _alert(name: str) -> SessionAlert:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    return SessionAlert.model_validate(payload)


def _placeholder_metrics(
    *, severity_exact: float, critical_recall: float, cost_mean_usd: Decimal
) -> RunMetrics:
    placeholder_pr = PR(precision=0.0, recall=0.0, support=0)
    return RunMetrics(
        n_cases=1,
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
        cost_total_usd=cost_mean_usd,
        latency_p50_ms=10,
        latency_p95_ms=10,
        per_severity=dict.fromkeys(range(1, 6), placeholder_pr),
        confusion=tuple(tuple(0 for _ in range(6)) for _ in range(5)),
        category_confusion={},
        sev_macro_f1=1.0,
    )


def _verdict_json(severity: int) -> str:
    action = "escalate and rotate credentials." if severity >= 4 else "monitor."
    return json.dumps(
        {
            "severity": severity,
            "category": "brute_force",
            "confidence": 0.9,
            "reasoning": "matches the golden label exactly for the gate CLI extra-coverage test.",
            "recommended_action": action,
            "escalate": severity >= 4,
        }
    )


# --- evals.gate.load_baseline: invalid (non-missing) JSON --------------------------------------


def test_load_baseline_invalid_json_raises(tmp_path: Path) -> None:
    """A baseline file that exists but is not valid JSON is a `ValueError`, same as a missing
    one — `evals.gate` never invents a baseline (PRD §7.4)."""
    path = tmp_path / "corrupt-baseline.json"
    path.write_text("not valid json{{{")

    with pytest.raises(ValueError):
        load_baseline(path)


# --- evals.run --write-baseline: the happy paths ------------------------------------------------


def test_write_baseline_creates_new_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=4, category="brute_force", escalate=True),
        labeler_note="human-labeled v2 case for the --write-baseline happy-path test.",
        labeled_by="human",
        labeled_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    golden_path = tmp_path / "v2.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    baseline_path = tmp_path / "baseline.json"
    fake = FakeLLMClient([_verdict_json(4)])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--write-baseline",
            "--baseline",
            str(baseline_path),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert f"baseline written to {baseline_path}" in captured.out

    loaded = load_baseline(baseline_path)
    assert loaded.prompt_version == "triage-v1"
    assert loaded.model_config == {
        "model": "fake-model",
        "judge_model": "",
        "replay_strict": True,  # v2-named golden -> strict replay by default
        "judge": False,
        "strong_model": "",
        "prompt_version": "triage-v1",
    }
    assert loaded.metrics.severity_exact == 1.0
    assert loaded.metrics.critical_recall == 1.0


def test_write_baseline_force_overwrites_and_prints_old_vs_new(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=4, category="brute_force", escalate=True),
        labeler_note="human-labeled v2 case for the --force-baseline happy-path test.",
        labeled_by="human",
        labeled_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    golden_path = tmp_path / "v2.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    baseline_path = tmp_path / "baseline.json"

    old_metrics = _placeholder_metrics(
        severity_exact=0.5, critical_recall=0.5, cost_mean_usd=Decimal("0.000100")
    )
    write_baseline(
        baseline_path,
        metrics=old_metrics,
        git_sha="oldsha0",
        prompt_version="triage-v1",
        model_config={
            "model": "fake-model",
            "judge_model": "",
            "replay_strict": True,
            "judge": False,
            "strong_model": "",
            "prompt_version": "triage-v1",
        },
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )

    fake = FakeLLMClient([_verdict_json(4)])
    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--write-baseline",
            "--force-baseline",
            "--baseline",
            str(baseline_path),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert "--force-baseline: overwriting" in captured.out
    assert "severity_exact 0.5 -> 1.0" in captured.out
    assert f"baseline written to {baseline_path}" in captured.out

    loaded = load_baseline(baseline_path)
    assert loaded.git_sha != "oldsha0"
    assert loaded.metrics.severity_exact == 1.0


# --- evals.run --gate: the passing path ---------------------------------------------------------


def test_gate_flag_passes_and_prints_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = GoldenCase(
        alert=_alert("alert1.json"),
        label=GoldenLabel(severity=4, category="brute_force", escalate=True),
        labeler_note="critical-severity case for the --gate PASS happy-path test.",
    )
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    baseline_path = tmp_path / "baseline.json"

    write_baseline(
        baseline_path,
        metrics=_placeholder_metrics(
            severity_exact=1.0, critical_recall=1.0, cost_mean_usd=Decimal("0.000100")
        ),
        git_sha="baseline0",
        prompt_version="triage-v1",
        model_config={
            "model": "fake-model",
            "judge_model": "",
            "replay_strict": False,
            "judge": False,
            "strong_model": "",
            "prompt_version": "triage-v1",
        },
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )

    fake = FakeLLMClient([_verdict_json(4)])
    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--gate",
            "--baseline",
            str(baseline_path),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert "GATE: PASS" in captured.out
    assert "GATE: FAIL" not in captured.out
