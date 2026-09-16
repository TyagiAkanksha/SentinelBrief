"""Pins the m7 task-02 determinism proof (PRD §7.2, `.claude/rules/evals.md`): "the same golden
file, prompt and model must give the same tool inputs on every run, so the only nondeterminism
left is the LLM's." Two `evals.run` invocations over the same golden set, prompt version and
`FakeLLMClient` script, replayed under `--replay-strict` against the same committed fixtures
(`tests/fixtures/tools/`), must produce byte-identical per-case results — tool-call counts,
verdicts, costs, errors — everything except the wall-clock `started_at` stamp.

`evals.run` does not recognise `--replay-strict` yet, so this test is RED via a `usage` exit
(`error: usage: unrecognized argument(s) ...`), not a collection-time import error — it pins
`evals.run` directly, not `evals.record` or the recorder seam (see `tests/test_record.py` and
`tests/test_replay_strict.py` for those).

Builds its own tiny golden set from `fixtures/alerts/*.json` (never `evals/golden/v1.jsonl`, per
`.claude/rules/evals.md`) and injects `tests.fakes.FakeLLMClient`, so nothing here touches the
network or a live API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.run import main
from tests.fakes import FakeLLMClient, ScriptedToolCall

FIXTURES_DIR = Path("fixtures/alerts")

_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials, no successful login observed.",
    "recommended_action": "monitor for continued brute-force activity.",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)

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
    """A fake, zero-priced model and no real API key for every test in this module."""
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
    return GoldenCase(
        alert=_alert(name),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for the evals.run determinism proof, not a scored claim.",
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def _scripted_responses() -> list[object]:
    # alert4's src_ip (192.0.2.55) has a committed get_ip_geo_asn fixture
    # (tests/fixtures/tools/get_ip_geo_asn/70c94a209a3ec9bd.json, country=DE); alert1 makes no
    # tool call.
    return [
        [ScriptedToolCall("get_ip_geo_asn", {"ip": "192.0.2.55"})],
        VALID_VERDICT_JSON,
        VALID_VERDICT_JSON,
    ]


def test_two_replayed_runs_produce_identical_tool_call_sequences(tmp_path: Path) -> None:
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert4.json"), _golden_case("alert1.json")]
    )

    out1 = tmp_path / "run1"
    out1.mkdir()
    rc1 = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(out1),
            "--tool-fixtures",
            "tests/fixtures/tools",
            "--replay-strict",
        ],
        llm=FakeLLMClient(_scripted_responses()),
    )

    out2 = tmp_path / "run2"
    out2.mkdir()
    rc2 = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(out2),
            "--tool-fixtures",
            "tests/fixtures/tools",
            "--replay-strict",
        ],
        llm=FakeLLMClient(_scripted_responses()),
    )

    assert rc1 == 0
    assert rc2 == 0
    written1 = sorted(out1.glob("*.json"))
    written2 = sorted(out2.glob("*.json"))
    assert len(written1) == 1
    assert len(written2) == 1
    payload1 = json.loads(written1[0].read_text())
    payload2 = json.loads(written2[0].read_text())
    # `started_at` legitimately differs between two real invocations; everything else — the
    # metrics and every per-case tool-call count, verdict, cost and error — must be identical.
    payload1.pop("started_at")
    payload2.pop("started_at")
    assert payload1 == payload2
    assert payload1["cases"][0]["tool_calls"] == 1
    assert payload1["cases"][1]["tool_calls"] == 0
