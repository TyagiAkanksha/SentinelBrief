"""Pins `evals.run`'s price-before-spend guard for an unpriced `--judge-model` (whole-branch fix
wave, finding t03 N4).

The judge tier is a BILLED model (PRD §7.3), so an explicit `--judge --judge-model <id absent from
MODEL_PRICES_JSON>` must exit 1 `config_error` naming the model BEFORE any case runs — no LLM call,
no per-run JSON written — exactly like the unpriced `--model`/`--strong-model` guards. The check
only applies on the real-client path (`llm=None`), so this test drives that path and proves nothing
was spent by asserting no per-run JSON landed in `--output-dir`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel

# `evals.run` is imported lazily inside the test through its `main` entrypoint below.
from evals.run import main

FIXTURES_DIR = Path("fixtures/alerts")

_SETTINGS_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_JSON_MODE",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "JUDGE_PROMPT_VERSION",
    "ENVIRONMENT",
)


@pytest.fixture(autouse=True)
def _fake_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A priced cheap model, no strong tier, and no real API key: the cheap price check passes so
    the run reaches the judge-model price check, which is the guard under test."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}'
    )


def _v2_golden(tmp_path: Path) -> Path:
    alert = SessionAlert.model_validate(json.loads((FIXTURES_DIR / "alert1.json").read_text()))
    case = GoldenCase(
        alert=alert,
        label=GoldenLabel(severity=4, category="brute_force", escalate=True),
        labeler_note="human-labeled v2 case for the unpriced --judge-model guard test.",
        labeled_by="human",
        labeled_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    path = tmp_path / "v2.jsonl"
    path.write_text(case.model_dump_json() + "\n")
    return path


def test_unpriced_judge_model_exit_1_config_error_no_spend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _v2_golden(tmp_path)
    output_dir = tmp_path / "results"

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--judge",
            "--judge-model",
            "ghost-judge",  # absent from MODEL_PRICES_JSON
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ]
        # no llm= kwarg: exercises the real-client price-before-spend path.
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "ghost-judge" in lines[0]
    assert "Traceback" not in captured.err
    assert not output_dir.exists() or list(output_dir.glob("*.json")) == []
