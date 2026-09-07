"""Pins `worker.triage_one.main`'s CLI exit-code contract (m0 task-05, PRD §12 M0).

`main(argv, *, llm=None) -> int` is driven in-process via `capsys`: stdout is one JSON verdict
document on success (exit 0), a single `error: <code>: <message>` line on stderr for exit 1
(usage/file/`ConfigError`/`LLMCallError`), and a single
`error: verdict_validation: failed after 2 attempts: <last_error>` line with no traceback for exit
2 (`VerdictValidationError`, the one PRD §6.5 retry exhausted). `llm=` injects `FakeLLMClient` so
these tests never touch the network (CONVENTIONS.md §10); the config-error test passes `llm=None`
so `main` builds the real client via `OpenAICompatibleLLMClient.from_settings`, which fails fast
on the empty `CHEAP_MODEL` before any network call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.errors import LLMCallError
from tests.fakes import FakeLLMClient
from worker.triage_one import main

# The task brief's fixed valid-verdict example, redefined locally (tests/test_triage_pipeline.py
# owns the same constant; test files never import from each other).
_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials",
    "recommended_action": "monitor",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)

FIXTURES_DIR = Path("fixtures/alerts")
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("alert*.json"))

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


def test_fixture_count_is_five() -> None:
    assert len(FIXTURE_PATHS) == 5


def test_prints_verdict_json_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["fixtures/alerts/alert1.json"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    out = json.loads(captured.out)
    assert set(out.keys()) == {
        "verdict",
        "model",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "retried",
    }
    assert 1 <= out["verdict"]["severity"] <= 5
    assert out["model"] == "fake-model"
    assert out["prompt_version"] == "triage-v1"
    assert isinstance(out["cost_usd"], str)


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_runs_all_five_fixtures(fixture_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main([str(fixture_path)], llm=fake)

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert 1 <= out["verdict"]["severity"] <= 5


def test_exit_2_on_verdict_validation_error_no_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeLLMClient(["{}", "{}"])

    rc = main(["fixtures/alerts/alert1.json"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: verdict_validation:")
    assert "after 2 attempts" in lines[0]
    assert "Traceback" not in captured.err


def test_exit_1_on_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["fixtures/alerts/nope.json"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error:")
    assert "Traceback" not in captured.err


def test_exit_1_on_llm_call_error(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLLMClient([LLMCallError("boom")])

    rc = main(["fixtures/alerts/alert1.json"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: llm_call_failed:")


def test_exit_1_on_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHEAP_MODEL", "")

    rc = main(["fixtures/alerts/alert1.json"], llm=None)

    captured = capsys.readouterr()
    assert rc == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")


def test_prompt_flag_unknown_version_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["fixtures/alerts/alert1.json", "--prompt", "does-not-exist"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")


def test_usage_error_missing_path_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing required argument is a clean exit 1, not argparse's own `SystemExit(2)`.

    Exit code 2 is reserved for `VerdictValidationError` (PRD §12 M0 / Interfaces block); a usage
    error colliding with it would make the exit code ambiguous to a caller.
    """
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main([], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_usage_error_unknown_flag_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown flag is a clean exit 1 with one stderr line, not argparse's `SystemExit(2)`."""
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["fixtures/alerts/alert1.json", "--bogus"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")
