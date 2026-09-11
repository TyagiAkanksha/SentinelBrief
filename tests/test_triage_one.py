"""Pins `worker.triage_one.main`'s CLI exit-code contract (m0 task-05, PRD §12 M0).

`main(argv, *, llm=None) -> int` is driven in-process via `capsys`: stdout is one JSON verdict
document on success (exit 0), a single `error: <code>: <message>` line on stderr for exit 1
(usage/file/`ConfigError`/`LLMCallError`), and a single
`error: verdict_validation: failed after 2 attempts: <last_error>` line with no traceback for exit
2 (`VerdictValidationError`, the one PRD §6.5 retry exhausted). `llm=` injects `FakeLLMClient` so
these tests never touch the network (CONVENTIONS.md §10); the config-error test passes `llm=None`
so `main` builds the real client via `OpenAICompatibleLLMClient.from_settings`, which fails fast
on the empty `CHEAP_MODEL` before any network call.

m5 task-03 (PRD §6.4) adds `--strong-model` and the two routing fields the output JSON gains
(`model_primary`, `escalated_model`) — present on every run, not only an escalating one.

m5 task-03 fix-1 (review M3) adds `--strong-model`'s own price-before-spend pin, mirroring
`evals.run`'s (this CLI had none).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.errors import LLMCallError
from tests.fakes import FakeLLMClient
from tests.helpers import VALID4, VALID4_STRONG
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
        "model_primary",
        "escalated_model",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "retried",
    }
    assert 1 <= out["verdict"]["severity"] <= 5
    assert out["model"] == "fake-model"
    assert out["prompt_version"] == "triage-v4"
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


# --- m0 final-review fix wave (I1, I3): additive only, no existing test/helper changed above. ---


def test_exit_1_on_malformed_model_prices_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Settings()` itself is unguarded at `main`'s entry (I1a): a malformed `MODEL_PRICES_JSON`
    in the environment must become a clean `config_error` exit 1, not a pydantic traceback —
    including before argparse ever runs.
    """
    fake = FakeLLMClient([VALID_VERDICT_JSON])
    monkeypatch.setenv("MODEL_PRICES_JSON", "not-json")

    rc = main(["fixtures/alerts/alert1.json"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err


def test_exit_1_on_unpriced_model_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """A `ConfigError` raised from inside `pipeline.run` (I1b) must not escape `main` as a
    traceback. `CHEAP_MODEL=fake-model` (set by the autouse fixture) is priced, so `llm=None`
    makes `main` build the real `OpenAICompatibleLLMClient` via `from_settings` — which succeeds,
    since it only checks `cheap_model`/`strong_model`. `--model unpriced-model` then reaches
    `complete_structured`'s price lookup, which (after the I2 fix) runs before any HTTP call, so
    this exercises the real client/pipeline path with no network touched — no `MockTransport` or
    fake needed to prove that.
    """
    rc = main(["fixtures/alerts/alert1.json", "--model", "unpriced-model"], llm=None)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err


def test_exit_1_on_unpriced_strong_model_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """m5 task-03 fix-1 (review M3): mirrors `test_exit_1_on_unpriced_model_flag` for
    `--strong-model`. `CHEAP_MODEL=fake-model` (the autouse fixture) is priced; `--strong-model
    ghost` is not. `LLM_API_KEY`/`LLM_BASE_URL` (an unroutable address) are set so that if the
    price check ran too late — or not at all — the cheap tier's own real call would hang or fail
    as `llm_call_failed` instead of a clean, fast `config_error`; that would also mean an
    `OpenAICompatibleLLMClient` got constructed, which this pin forbids ("no LLM constructed").
    """
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")

    rc = main(["fixtures/alerts/alert1.json", "--strong-model", "ghost"], llm=None)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "ghost" in lines[0]
    assert "Traceback" not in captured.err


def test_exit_1_on_malformed_alert_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The invalid-alert-file half of the exit-1 contract (I3a) was unpinned: mutation M-D showed
    dropping `json.JSONDecodeError`/`ValidationError` from the handler left every CLI test green.
    """
    fake = FakeLLMClient([VALID_VERDICT_JSON])
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not json")

    rc = main([str(bad_file)], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_alert:")
    assert "Traceback" not in captured.err


def test_exit_1_on_schema_invalid_alert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Valid JSON that fails `SessionAlert` schema validation (missing `events`) is the other half
    of I3a — the same unpinned path, reached via `pydantic.ValidationError` instead of
    `json.JSONDecodeError`.
    """
    fake = FakeLLMClient([VALID_VERDICT_JSON])
    bad_file = tmp_path / "no_events.json"
    bad_file.write_text(
        json.dumps(
            {
                "source": "cowrie",
                "session_id": "abc123",
                "src_ip": "203.0.113.5",
                "sensor": "hp-test-01",
            }
        )
    )

    rc = main([str(bad_file)], llm=fake)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_alert:")
    assert "Traceback" not in captured.err


def test_model_flag_overrides_default(capsys: pytest.CaptureFixture[str]) -> None:
    """`--model` overrides the `Settings().cheap_model` default (I3b, named in the task-05
    Interfaces block but never asserted).
    """
    fake = FakeLLMClient([VALID_VERDICT_JSON])

    rc = main(["fixtures/alerts/alert1.json", "--model", "other-model"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 0
    out = json.loads(captured.out)
    assert out["model"] == "other-model"
    assert fake.calls[0].model == "other-model"


# --- m5 task-03: --strong-model, PRD §6.4 -------------------------------------------------------


def test_strong_model_flag_escalates_and_prints_routing_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeLLMClient([VALID4, VALID4_STRONG])

    rc = main(["fixtures/alerts/alert1.json", "--strong-model", "strong-model"], llm=fake)

    captured = capsys.readouterr()
    assert rc == 0
    out = json.loads(captured.out)
    assert out["model_primary"] == "fake-model"
    assert out["escalated_model"] is True
    assert out["model"] == "strong-model"

    # Without the flag, and STRONG_MODEL unset (the autouse fixture clears it): routing stays
    # off, so `escalated_model` is false regardless of the verdict's own severity/confidence.
    fake_no_strong = FakeLLMClient([VALID_VERDICT_JSON])

    rc_no_strong = main(["fixtures/alerts/alert1.json"], llm=fake_no_strong)

    captured_no_strong = capsys.readouterr()
    assert rc_no_strong == 0
    out_no_strong = json.loads(captured_no_strong.out)
    assert out_no_strong["escalated_model"] is False
    assert out_no_strong["model_primary"] == "fake-model"
