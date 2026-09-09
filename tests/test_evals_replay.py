"""Pins `evals.run`'s `--tool-fixtures DIR` flag (PRD §7.2: evals run the full pipeline against
recorded tool fixtures; `.claude/rules/evals.md`: the LLM is the only live component of an eval
run, external tools always replay) — m4 task-06.

Every test builds its own tiny golden set from `fixtures/alerts/*.json` (never
`evals/golden/v1.jsonl`, per `.claude/rules/evals.md`) and injects `tests.fakes.FakeLLMClient`, so
nothing here touches the network. `--concurrency 1` throughout (controller ruling R3) so the
fake's pop order and `fake.calls` indices are deterministic against golden-file (== case) order.
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

# A minimally-valid Verdict reply, redefined locally (test files never import from each other,
# per `tests/test_evals_run.py`'s own docstring note).
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
        labeler_note="synthetic label for evals.run --tool-fixtures plumbing tests, not scored.",
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def test_main_replays_external_tools_from_the_fixtures_dir_and_records_tool_call_counts(
    tmp_path: Path,
) -> None:
    # alert4's src_ip (192.0.2.55) has a get_ip_geo_asn fixture answering country=DE
    # (tests/fixtures/tools/get_ip_geo_asn/70c94a209a3ec9bd.json); alert1 gets no tool call.
    golden_path = _write_golden(
        tmp_path / "golden.jsonl", [_golden_case("alert4.json"), _golden_case("alert1.json")]
    )
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    fake = FakeLLMClient(
        [
            [ScriptedToolCall("get_ip_geo_asn", {"ip": "192.0.2.55"})],
            VALID_VERDICT_JSON,
            VALID_VERDICT_JSON,
        ]
    )

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
            "--tool-fixtures",
            "tests/fixtures/tools",
        ],
        llm=fake,
    )

    assert rc == 0
    written = sorted(output_dir.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    tool_call_counts = [case["tool_calls"] for case in payload["cases"]]
    assert tool_call_counts == [1, 0]
    assert len(fake.calls) == 3
    assert "DE" in fake.calls[1].messages[-1]["content"]


def test_tool_fixtures_default_and_non_directory_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = _write_golden(tmp_path / "golden.jsonl", [_golden_case("alert1.json")])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    # No --tool-fixtures: DEFAULT_TOOL_FIXTURES (tests/fixtures/tools) is used and the run still
    # succeeds — the case's src_ip has no tool call scripted, so no fixture lookup is needed.
    fake_default = FakeLLMClient([VALID_VERDICT_JSON])
    rc_default = main(
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
        llm=fake_default,
    )
    capsys.readouterr()
    assert rc_default == 0

    fake_bad_dir = FakeLLMClient([])
    rc_bad_dir = main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--tool-fixtures",
            "/nonexistent-tool-fixtures-dir",
        ],
        llm=fake_bad_dir,
    )

    captured = capsys.readouterr()
    assert rc_bad_dir == 1
    assert captured.out == ""
    assert captured.err == "error: usage: --tool-fixtures is not a directory\n"
    assert fake_bad_dir.calls == []
