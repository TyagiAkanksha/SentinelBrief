"""Pins `evals.run`: `run_golden`, `main`, the CLI flags, and every failure path (m1 task-03).

PRD §7.2 (CLI shape: repeatable `--prompt`, `--model`, `--concurrency`, `--output-dir`), §7.3
(the run drives the **real** `worker.triage.TriagePipeline`, never a reimplementation); the
task-03 brief's failure-path table (usage / config_error(Settings) / invalid_golden /
config_error(pipeline) / output_error, plus the separate `all_cases_failed` path) and its
"price before spend" note (an unpriced `--model` fails at `TriagePipeline`/`from_settings`
construction, never mid-run).

Every test builds its own tiny golden set from `fixtures/alerts/alert*.json` via `SessionAlert` +
`GoldenLabel` (never `evals/golden/v1.jsonl`, per `.claude/rules/evals.md`: v1 is real dataset
content, not test fixture material) and injects `tests.fakes.FakeLLMClient` so nothing here
touches the network. `evals.run` does not exist yet, so every test in this module is RED at
collection with `ModuleNotFoundError: No module named 'evals.run'`, not merely at first use.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.run import main, run_golden
from evals.scoring import COLUMNS, CaseResult, RunMetrics
from tests.fakes import FakeLLMClient
from worker.triage import TriagePipeline

FIXTURES_DIR = Path("fixtures/alerts")

# A minimally-valid Verdict reply, redefined locally (tests/test_triage_one.py owns the same
# constant; test files never import from each other, per that module's own docstring).
_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials, no successful login observed.",
    "recommended_action": "monitor for continued brute-force activity.",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)

# Every env var Settings maps to (CONVENTIONS.md §7 / .env.example); cleared before each test so a
# developer's shell (in particular a real LLM_API_KEY) can never leak into the CLI under test.
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
    """A fake, zero-priced model and no real API key for every test in this module by default."""
    for name in _SETTINGS_ENV_VARS:
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
    irrelevant to every test in this module (none of them score accuracy)."""
    return GoldenCase(
        alert=_alert(name),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic label for evals.run CLI plumbing tests, not a scored claim.",
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


# --- run_golden -------------------------------------------------------------------------------


def test_run_golden_one_result_per_case_in_order() -> None:
    cases = [_golden_case("alert1.json"), _golden_case("alert2.json"), _golden_case("alert3.json")]
    fake = FakeLLMClient([VALID_VERDICT_JSON, VALID_VERDICT_JSON, VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    results = asyncio.run(run_golden(cases, pipeline=pipeline, concurrency=1))

    assert [r.case_id for r in results] == [c.case_id for c in cases]
    assert all(r.verdict is not None for r in results)
    assert all(r.error is None for r in results)


def test_run_golden_captures_pipeline_failure_as_error() -> None:
    cases = [_golden_case("alert1.json"), _golden_case("alert2.json")]
    # First case fails validation twice (VerdictValidationError); second case succeeds.
    fake = FakeLLMClient(["{}", "{}", VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    results = asyncio.run(run_golden(cases, pipeline=pipeline, concurrency=1))

    assert len(results) == 2
    first, second = results
    assert first.case_id == cases[0].case_id
    assert first.verdict is None
    assert first.error is not None
    assert first.error.startswith("verdict_validation")
    # M0: tokens/cost for a failed case are unknown to the pipeline -> recorded as 0.
    assert first.input_tokens == 0
    assert first.output_tokens == 0
    assert first.cost_usd == Decimal("0")
    assert second.case_id == cases[1].case_id
    assert second.verdict is not None
    assert second.error is None


# --- main: success paths -----------------------------------------------------------------------


def test_main_prints_one_row_per_prompt_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON] * 4)  # 2 cases x 2 prompt runs

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    lines = captured.out.rstrip("\n").splitlines()
    assert lines[0] == "| " + " | ".join(COLUMNS) + " |"
    assert lines[1] == "|" + "|".join("---" for _ in COLUMNS) + "|"
    assert len(lines) == 4  # header + separator + 2 body rows


def test_main_passes_prompt_versions_to_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1_text = Path("worker/prompts/triage-v1.md").read_text()
    (tmp_path / "triage-v1.md").write_text(v1_text)
    (tmp_path / "triage-v2.md").write_text(
        v1_text + "\n\n<!-- test-marker-distinct-v2-sentence -->\n"
    )
    monkeypatch.setattr("worker.prompts.PROMPTS_DIR", tmp_path)

    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient([VALID_VERDICT_JSON, VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v2",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
        ],
        llm=fake,
    )

    assert rc == 0
    assert len(fake.calls) == 2
    system_v1 = fake.calls[0].messages[0]["content"]
    system_v2 = fake.calls[1].messages[0]["content"]
    assert system_v1 != system_v2
    assert "test-marker-distinct-v2-sentence" in system_v2
    assert "test-marker-distinct-v2-sentence" not in system_v1


def test_main_writes_result_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts_dir = tmp_path / "_prompts"
    prompts_dir.mkdir()
    v1_text = Path("worker/prompts/triage-v1.md").read_text()
    (prompts_dir / "triage-v1.md").write_text(v1_text)
    (prompts_dir / "triage-v2.md").write_text(v1_text + "\n\n<!-- v2 -->\n")
    monkeypatch.setattr("worker.prompts.PROMPTS_DIR", prompts_dir)

    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    fake = FakeLLMClient([VALID_VERDICT_JSON] * 4)  # 2 cases x 2 prompt runs

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v2",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
        llm=fake,
    )

    assert rc == 0
    written = sorted(tmp_path.glob("*.json"))  # non-recursive: excludes _prompts/*.md
    assert len(written) == 2
    metrics_fields = {f.name for f in fields(RunMetrics)}
    case_fields = {f.name for f in fields(CaseResult)}
    seen_prompts = set()
    for path in written:
        assert re.fullmatch(r"\d{8}T\d{6}Z-triage-v[12]\.json", path.name)
        payload = json.loads(path.read_text())
        assert set(payload.keys()) == {
            "prompt_version",
            "model",
            "git_sha",
            "started_at",
            "metrics",
            "cases",
        }
        assert payload["model"] == "fake-model"
        assert isinstance(payload["git_sha"], str) and payload["git_sha"]
        assert isinstance(payload["started_at"], str) and payload["started_at"]
        assert set(payload["metrics"].keys()) == metrics_fields
        assert len(payload["cases"]) == 2
        first_case = payload["cases"][0]
        assert set(first_case.keys()) == case_fields
        assert "case_id" in first_case
        assert "error" in first_case
        assert isinstance(first_case["verdict"], dict)
        seen_prompts.add(payload["prompt_version"])
    assert seen_prompts == {"triage-v1", "triage-v2"}


# --- main: failure paths (task-03 brief's failure-path table) -----------------------------------


def test_main_exit_1_on_missing_golden_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_path = tmp_path / "does-not-exist.jsonl"
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(missing_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_golden:")
    assert "Traceback" not in captured.err


def test_main_usage_error_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["--golden", str(golden_path)], llm=fake)  # no --prompt

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_main_exit_1_on_malformed_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MODEL_PRICES_JSON", "not-json")
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err


def test_main_exit_1_on_invalid_golden_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alert_payload = json.loads((FIXTURES_DIR / "alert1.json").read_text())
    bad_row = {
        "alert": alert_payload,
        "label": {"severity": 9, "category": "scanning", "escalate": True},
        "labeler_note": "deliberately invalid severity for the invalid-golden-row failure path.",
        "tags": [],
    }
    golden_path = tmp_path / "invalid.jsonl"
    golden_path.write_text(json.dumps(bad_row) + "\n")
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_golden:")
    assert "row 1" in lines[0]
    assert "Traceback" not in captured.err


def test_main_exit_1_on_unknown_prompt_before_any_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    fake = FakeLLMClient([])  # no calls should ever reach it

    rc = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "nope",
            "--concurrency",
            "1",
            "--output-dir",
            str(tmp_path / "results"),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err
    assert fake.calls == []


def test_main_exit_1_when_all_cases_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert1.json"), _golden_case("alert2.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient(["{}", "{}", "{}", "{}"])  # both cases fail both attempts

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
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: all_cases_failed:")
    assert "Traceback" not in captured.err
    # Price before spend already happened; the run's JSON result is still written.
    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
