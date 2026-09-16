"""Pins ruling R25 (m7 task-02 fix-1, review C1): an `unavailable(...)` result whose `reason` is
TRANSIENT (an environment failure — no API key, no `.mmdb`, a quota hit, a network blip, an
unknown tool) is never persisted as a fixture — `record()` removes the file `LiveToolRecorder`
already wrote, reports the call under `failed` with its tool/key/reason, and `main` exits 1. A
DETERMINISTIC `unavailable(...)` (e.g. `invalid_arguments`) is the opposite: it IS persisted, same
as a real answer, because the tool's own logic — not the owner's environment — produced it and it
will reproduce identically on every future run. Strict replay additionally refuses to SERVE a
fixture whose recorded reason is transient, even if one was hand-written to disk, so a poisoned
file committed by hand can never become tool evidence (`FixtureMissingError` with `":poisoned"` in
its message).

Also pins review I2 (the "MISSING FIXTURES" line must print even when every case is missing its
fixture — today it is suppressed by the `all_cases_failed` early return) and review I5 (the
`--only` flag and `main`'s all-recorded success exit, both previously untested).

`evals.record.record`/`main` do not yet special-case a transient reason (every `unavailable(...)`
result is currently reported the same way, and the file it minted is never removed), and
`ReplayToolRecorder` does not yet refuse a poisoned fixture, so most tests in this module are RED
against the current implementation for those reasons; the I2 test is RED because `evals.run.main`
prints the table/missing-fixtures block only *after* the `all_cases_failed` short-circuit, which
fires first when every case is missing its fixture.

Every test that needs BOTH the async `record()` and the synchronous `evals.record.main` in the
same body stays a plain `def` test wrapping the async half in `asyncio.run(...)` — never
`async def` — mirroring the R27-approved shape of `tests/test_record.py::
test_failed_tool_reported_by_class_only`, so no test in this file could ever justify reintroducing
the thread bridge R27 just removed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from core.errors import FixtureMissingError
from core.schemas.alert import CowrieEvent, SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.record import main as record_main
from evals.record import record
from evals.run import main as run_main
from tests.fakes import FakeLLMClient, ScriptedToolCall
from worker.tools import (
    ToolContext,
    ToolRegistry,
    fixture_key,
    fixture_path,
    unavailable,
    write_fixture,
)
from worker.tools.recorder import LiveToolRecorder, ReplayToolRecorder

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


def _alert(name: str, *, src_ip: str) -> SessionAlert:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    payload["src_ip"] = src_ip
    return SessionAlert.model_validate(payload)


def _case(name: str, *, src_ip: str) -> GoldenCase:
    return GoldenCase(
        alert=_alert(name, src_ip=src_ip),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for evals.record poison-fixture tests, not a scored claim.",
    )


def _run_golden_case(
    name: str, *, src_ip: str, labeled_by: Literal["human"] | None = None
) -> GoldenCase:
    return GoldenCase(
        alert=_alert(name, src_ip=src_ip),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for evals.run missing-fixtures-line plumbing, not scored.",
        labeled_by=labeled_by,
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def _make_ctx() -> ToolContext:
    alert = SessionAlert(
        source="cowrie",
        session_id="s1",
        src_ip="203.0.113.10",
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session="s1",
                src_ip="203.0.113.10",
                sensor="sensor-1",
            )
        ],
    )
    return ToolContext(alert=alert, session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


class _CannedExternalTool:
    """A canned external-seam `Tool` stub answering a fixed successful result — the external
    seam, not a mock of our own code (CONVENTIONS.md §10)."""

    description = "canned external tool stub for evals.record poison-fixture tests"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": ["ip"],
        "additionalProperties": False,
    }
    external = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.run_count = 0

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        self.run_count += 1
        return {"ip": arguments["ip"], "tool": self.name}


class _ReasonExternalTool:
    """An external-seam `Tool` stub that always answers `unavailable(reason)` for a
    caller-chosen `reason` — stands in for a real tool's own failure paths (`no_api_key`,
    `quota_exceeded`, `network_error`, `invalid_arguments`, ...) without ever touching a real API.
    """

    description = "reason-returning external tool stub for evals.record poison-fixture tests"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": ["ip"],
        "additionalProperties": False,
    }
    external = True

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        self.run_count = 0

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        self.run_count += 1
        return unavailable(self.reason)


def _registry(fixtures_dir: Path, *, tools: list[Any]) -> ToolRegistry:
    return ToolRegistry(
        tools, recorder=LiveToolRecorder(record_dir=fixtures_dir), max_result_chars=10_000
    )


# --- R25: transient reasons are never persisted -------------------------------------------------


@pytest.mark.parametrize("reason", ["no_api_key", "quota_exceeded", "network_error"])
def test_transient_unavailable_result_is_never_persisted_as_a_fixture(
    tmp_path: Path, reason: str
) -> None:
    fixtures_dir = tmp_path / "fixtures"
    ip = "203.0.113.90"
    cases = [_case("alert1.json", src_ip=ip)]
    good_tool = _CannedExternalTool("get_ip_geo_asn")
    bad_tool = _ReasonExternalTool("lookup_ip_reputation", reason)
    key = fixture_key({"ip": ip})

    report = asyncio.run(
        record(
            cases,
            registry=_registry(fixtures_dir, tools=[good_tool, bad_tool]),
            fixtures_dir=fixtures_dir,
            only=None,
            dry_run=False,
        )
    )

    # The recorder wrote SOMETHING for lookup_ip_reputation as a side effect of `registry.execute`
    # (LiveToolRecorder writes unconditionally) — `record()` must have removed it again.
    assert not fixture_path(fixtures_dir, "lookup_ip_reputation", {"ip": ip}).exists()
    assert fixture_path(fixtures_dir, "get_ip_geo_asn", {"ip": ip}).exists()  # the good call stands
    assert report.failed == [("lookup_ip_reputation", key, reason)]
    assert report.recorded == 1

    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(cases[0].model_dump_json() + "\n")
    rc = record_main(
        ["--golden", str(golden_path), "--fixtures", str(fixtures_dir)],
        registry=_registry(
            fixtures_dir,
            tools=[
                _CannedExternalTool("get_ip_geo_asn"),
                _ReasonExternalTool("lookup_ip_reputation", reason),
            ],
        ),
    )

    assert rc == 1


async def test_deterministic_unavailable_result_is_persisted_as_a_fixture(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    ip = "203.0.113.91"
    cases = [_case("alert1.json", src_ip=ip)]
    good_tool = _CannedExternalTool("get_ip_geo_asn")
    deterministic_tool = _ReasonExternalTool("lookup_ip_reputation", "invalid_arguments")

    report = await record(
        cases,
        registry=_registry(fixtures_dir, tools=[good_tool, deterministic_tool]),
        fixtures_dir=fixtures_dir,
        only=None,
        dry_run=False,
    )

    assert fixture_path(fixtures_dir, "lookup_ip_reputation", {"ip": ip}).exists()
    assert report.failed == []
    assert report.recorded == 2

    # Idempotent: a second run treats the persisted deterministic-unavailable fixture as already
    # present, never re-attempting it and never re-flagging it as failed.
    report2 = await record(
        cases,
        registry=_registry(
            fixtures_dir,
            tools=[
                _CannedExternalTool("get_ip_geo_asn"),
                _ReasonExternalTool("lookup_ip_reputation", "invalid_arguments"),
            ],
        ),
        fixtures_dir=fixtures_dir,
        only=None,
        dry_run=False,
    )

    assert report2.recorded == 0
    assert report2.skipped_existing == 2
    assert report2.failed == []


async def test_strict_replay_refuses_a_hand_poisoned_fixture(tmp_path: Path) -> None:
    ip = "203.0.113.92"
    arguments = {"ip": ip}
    write_fixture(tmp_path, "lookup_ip_reputation", arguments, unavailable("no_api_key"))
    tool = _CannedExternalTool("lookup_ip_reputation")  # strict replay never calls .run
    recorder = ReplayToolRecorder(tmp_path, strict=True)
    ctx = _make_ctx()

    with pytest.raises(FixtureMissingError) as exc_info:
        await recorder.execute(tool, arguments, ctx)

    assert ":poisoned" in str(exc_info.value)
    assert tool.run_count == 0


# --- I2: the MISSING FIXTURES line must survive the all_cases_failed short-circuit ---------------


def test_missing_fixtures_line_prints_even_when_every_case_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures_dir = tmp_path / "tool_fixtures"
    fixtures_dir.mkdir()  # empty: nothing has ever been recorded
    ip = "203.0.113.93"
    case = _run_golden_case("alert1.json", src_ip=ip, labeled_by="human")
    golden_path = _write_golden(tmp_path / "v2-allmissing.jsonl", [case])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    fake = FakeLLMClient([[ScriptedToolCall("lookup_ip_reputation", {"ip": ip})]])

    rc = run_main(
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
    key = fixture_key({"ip": ip})
    assert rc == 1
    assert "MISSING FIXTURES (" in captured.out
    assert f"lookup_ip_reputation {key}" in captured.out


# --- I5: --only and main()'s all-recorded success exit -------------------------------------------


async def test_only_flag_restricts_recording_to_one_tool(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    ip = "203.0.113.94"
    cases = [_case("alert1.json", src_ip=ip)]
    registry = _registry(
        fixtures_dir,
        tools=[_CannedExternalTool("get_ip_geo_asn"), _CannedExternalTool("lookup_ip_reputation")],
    )

    report = await record(
        cases, registry=registry, fixtures_dir=fixtures_dir, only="get_ip_geo_asn", dry_run=False
    )

    assert report.planned == 1
    assert report.recorded == 1
    assert fixture_path(fixtures_dir, "get_ip_geo_asn", {"ip": ip}).exists()
    assert not fixture_path(fixtures_dir, "lookup_ip_reputation", {"ip": ip}).exists()


def test_main_records_and_exits_zero(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    ip = "203.0.113.95"
    case = _case("alert1.json", src_ip=ip)
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(case.model_dump_json() + "\n")
    registry = _registry(
        fixtures_dir,
        tools=[_CannedExternalTool("get_ip_geo_asn"), _CannedExternalTool("lookup_ip_reputation")],
    )

    rc = record_main(
        ["--golden", str(golden_path), "--fixtures", str(fixtures_dir)], registry=registry
    )

    assert rc == 0
    assert fixture_path(fixtures_dir, "get_ip_geo_asn", {"ip": ip}).exists()
    assert fixture_path(fixtures_dir, "lookup_ip_reputation", {"ip": ip}).exists()
