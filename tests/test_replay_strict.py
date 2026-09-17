"""Pins the strict-replay seam m7 task-02 adds: `ReplayToolRecorder(fixtures_dir, strict=True)`
raises `core.errors.FixtureMissingError` instead of degrading to `unavailable(...)` on a missing
fixture; `ToolRegistry.execute` lets that error propagate rather than swallowing it into its own
`unavailable(...)` backstop (controller ruling Q6's carve-out); and `evals.run`'s
`--replay-strict/--no-replay-strict` flag (default strict for a v2-named golden file, PRD §13)
turns a per-case missing fixture into `CaseResult(error="fixture_missing:<tool>:<key>")` plus a
non-zero exit naming every missing `(tool, key)` pair — "a v2 case whose fixture is missing fails
the eval loudly rather than going live" (spine Global Constraints).

`ReplayToolRecorder` does not accept `strict=` yet and `core.errors` has no `FixtureMissingError`
yet, so every test in this module is RED at collection: `ImportError: cannot import name
'FixtureMissingError' from 'core.errors'`. The two `evals.run` tests near the bottom are RED for
a different, but equally expected, reason: `--replay-strict`/`--no-replay-strict` are not
recognised flags yet, so `main` returns a `usage` exit before ever reaching the new behavior.

Every case here is a synthetic `GoldenCase` built from `fixtures/alerts/*.json` shapes with
`labeled_by="human"` set explicitly IN THE TEST (test input, not a golden label — ruling R23)
wherever a v2-shaped case is needed, per `evals.run.is_v2_golden`'s `require_human` enforcement
(m7 task-01).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from core.errors import FixtureMissingError, SentinelBriefError
from core.schemas.alert import CowrieEvent, SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.run import main
from tests.fakes import FakeLLMClient, ScriptedToolCall
from worker.tools import ToolContext, ToolRegistry, fixture_key, unavailable, write_fixture
from worker.tools.recorder import ReplayToolRecorder

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


def _golden_case(
    name: str, *, src_ip: str, labeled_by: Literal["human"] | None = None
) -> GoldenCase:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    payload["src_ip"] = src_ip
    alert = SessionAlert.model_validate(payload)
    return GoldenCase(
        alert=alert,
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for evals.run --replay-strict plumbing tests, not scored.",
        labeled_by=labeled_by,
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def _make_alert(session_id: str = "s1") -> SessionAlert:
    """A minimal, valid `SessionAlert`: one connect event, no fixture/DB needed."""
    return SessionAlert(
        source="cowrie",
        session_id=session_id,
        src_ip="203.0.113.10",
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session=session_id,
                src_ip="203.0.113.10",
                sensor="sensor-1",
            )
        ],
    )


def _make_ctx() -> ToolContext:
    return ToolContext(alert=_make_alert(), session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


class ExternalStub:
    """An external-seam `Tool` stub (mirrors `tests/test_tool_recorder.py::ExternalStub`) with a
    call counter — replay must never call it, strict or not; the external seam, not a mock of our
    own code (CONVENTIONS.md §10).
    """

    name = "external_stub"
    description = "A stub external tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = True

    def __init__(self) -> None:
        self.run_count = 0

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        self.run_count += 1
        return {"live": True}


# --- FixtureMissingError itself -------------------------------------------------------------


def test_fixture_missing_error_is_a_sentinelbrief_error_with_the_right_code() -> None:
    err = FixtureMissingError("external_stub:deadbeefdeadbeef")

    assert isinstance(err, SentinelBriefError)
    assert err.code == "fixture_missing"


def test_fixture_missing_error_is_never_referenced_under_api() -> None:
    """R23: `FixtureMissingError` is a worker/evals error no route ever raises — `api/errors.py`'s
    status map is deliberately NOT extended for it, so nothing under `api/` should ever need to
    import or name it. Resolved from `process.cwd()` (M8a R20), never `import.meta.url`-style
    package-relative resolution, so this stays correct regardless of where pytest is invoked from.
    """
    api_dir = Path.cwd() / "api"
    offending = [
        path for path in api_dir.rglob("*.py") if "FixtureMissingError" in path.read_text()
    ]

    assert offending == []


# --- strict replay ---------------------------------------------------------------------------


async def test_strict_missing_fixture_raises_fixture_missing_error(tmp_path: Path) -> None:
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    recorder = ReplayToolRecorder(
        tmp_path, strict=True, strict_tools=frozenset({ExternalStub.name})
    )
    ctx = _make_ctx()

    with pytest.raises(FixtureMissingError) as exc_info:
        await recorder.execute(tool, arguments, ctx)

    key = fixture_key(arguments)
    assert str(exc_info.value) == f"{tool.name}:{key}"
    assert tool.run_count == 0


async def test_non_strict_missing_fixture_still_degrades_to_unavailable(tmp_path: Path) -> None:
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    ctx = _make_ctx()

    default_recorder = ReplayToolRecorder(tmp_path)  # strict defaults to False
    default_result = await default_recorder.execute(tool, arguments, ctx)

    explicit_recorder = ReplayToolRecorder(tmp_path, strict=False)
    explicit_result = await explicit_recorder.execute(tool, arguments, ctx)

    assert default_result == unavailable("fixture_missing")
    assert explicit_result == unavailable("fixture_missing")
    assert tool.run_count == 0


async def test_strict_present_fixture_still_replays_normally(tmp_path: Path) -> None:
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    write_fixture(tmp_path, tool.name, arguments, {"asn": 64512})
    recorder = ReplayToolRecorder(tmp_path, strict=True)
    ctx = _make_ctx()

    result = await recorder.execute(tool, arguments, ctx)

    assert result == {"asn": 64512}
    assert tool.run_count == 0


async def test_registry_does_not_swallow_fixture_missing_error(tmp_path: Path) -> None:
    """Mutant check: if the registry's `except Exception` backstop
    (`worker/tools/registry.py::ToolRegistry.execute`) ever catches `FixtureMissingError` instead
    of the one carved-out `except FixtureMissingError: raise` placed above it, this error is
    silently swallowed into `unavailable(...)` and the whole point of strict replay (a missing
    fixture fails the eval loudly) is lost — this test fails the moment that carve-out is removed
    or reordered below the catch-all.
    """
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    registry = ToolRegistry(
        [tool],
        recorder=ReplayToolRecorder(
            tmp_path, strict=True, strict_tools=frozenset({ExternalStub.name})
        ),
        max_result_chars=4000,
    )
    ctx = _make_ctx()

    with pytest.raises(FixtureMissingError):
        await registry.execute(tool.name, arguments, ctx)


# --- evals.run: --replay-strict / --no-replay-strict ------------------------------------------


def test_run_v2_defaults_strict_and_exits_1_listing_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures_dir = tmp_path / "tool_fixtures"
    fixtures_dir.mkdir()
    ok_ip = "203.0.113.150"
    missing_ip = "203.0.113.151"
    write_fixture(
        fixtures_dir,
        "lookup_ip_reputation",
        {"ip": ok_ip},
        {"ip": ok_ip, "abuse_score": 0, "reports": 0, "last_seen": None, "cached": False},
    )

    case_missing = _golden_case("alert1.json", src_ip=missing_ip, labeled_by="human")
    case_ok = _golden_case("alert2.json", src_ip=ok_ip, labeled_by="human")
    golden_path = _write_golden(tmp_path / "v2-tiny.jsonl", [case_missing, case_ok])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    fake = FakeLLMClient(
        [
            [ScriptedToolCall("lookup_ip_reputation", {"ip": missing_ip})],
            [ScriptedToolCall("lookup_ip_reputation", {"ip": ok_ip})],
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
            str(fixtures_dir),
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    missing_key = fixture_key({"ip": missing_ip})
    assert rc == 1
    assert "MISSING FIXTURES" in captured.out
    assert f"lookup_ip_reputation {missing_key}" in captured.out

    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    cases_by_id = {c["case_id"]: c for c in payload["cases"]}
    missing_result = cases_by_id[case_missing.case_id]
    ok_result = cases_by_id[case_ok.case_id]
    assert missing_result["error"] == f"fixture_missing:lookup_ip_reputation:{missing_key}"
    assert missing_result["verdict"] is None
    assert ok_result["error"] is None
    assert ok_result["verdict"] is not None


def test_no_replay_strict_flag_keeps_v1_behaviour(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures_dir = tmp_path / "tool_fixtures"
    fixtures_dir.mkdir()
    missing_ip = "203.0.113.161"
    case = _golden_case("alert1.json", src_ip=missing_ip, labeled_by="human")
    golden_path = _write_golden(tmp_path / "v2-no-strict.jsonl", [case])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    fake = FakeLLMClient(
        [
            [ScriptedToolCall("lookup_ip_reputation", {"ip": missing_ip})],
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
            str(fixtures_dir),
            "--no-replay-strict",
        ],
        llm=fake,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "MISSING FIXTURES" not in captured.out
    written = list(output_dir.glob("*-triage-v1.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["cases"][0]["error"] is None
    assert payload["cases"][0]["verdict"] is not None
    assert len(fake.calls) == 2
