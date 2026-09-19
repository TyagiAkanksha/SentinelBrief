"""Pins `evals.record`: `planned_calls`, `record`, `main` (m7 task-02, PRD §7.2).

`evals.record` walks every golden-set case and calls each EXTERNAL tool live once for every
argument set the pipeline can legitimately request for that case (`get_ip_geo_asn` and
`lookup_ip_reputation`, each keyed on the session's `src_ip` only), writing fixtures with the
existing `write_fixture` layout (`worker/tools/recorder.py`) — so a v2 case's fixture can be
minted once and replayed deterministically forever after (`.claude/rules/evals.md`).

`evals.record` does not exist yet, so every test in this module is RED at collection with
`ModuleNotFoundError: No module named 'evals.record'`.

Every case here is a synthetic `GoldenCase` built from `fixtures/alerts/*.json` shapes (never
`evals/golden/v1.jsonl` or `v2.jsonl`, per `.claude/rules/evals.md`: those are real dataset
content, not test fixture material) with a `src_ip` override so the de-duplication behavior is
exercisable without five different fixture alerts. Every "external" tool here is an in-file stub
(mirrors `tests/test_tool_recorder.py::ExternalStub`) — the external seam, not a mock of our own
code (CONVENTIONS.md §10) — so nothing in this module ever calls a real API.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.record import main, planned_calls, record
from worker.tools import LiveToolRecorder, ToolContext, ToolRegistry, fixture_key, fixture_path

FIXTURES_DIR = Path("fixtures/alerts")


def _alert(name: str, *, src_ip: str) -> SessionAlert:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    payload["src_ip"] = src_ip
    return SessionAlert.model_validate(payload)


def _case(name: str, *, src_ip: str) -> GoldenCase:
    """One structurally-valid golden case at `src_ip`; the label content is irrelevant to every
    test in this module (nothing here scores accuracy)."""
    return GoldenCase(
        alert=_alert(name, src_ip=src_ip),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for evals.record plumbing tests, not a scored claim.",
    )


class _CannedExternalTool:
    """A canned external-seam `Tool` stub answering `{"ip": ..., "tool": self.name}` — the
    external seam `evals.record` is meant to call, never a mock of our own code.
    """

    description = "canned external tool stub for evals.record tests"
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


class _RaisingExternalTool:
    """Violates the "tools never raise" contract on purpose, so `ToolRegistry.execute`'s own
    backstop (`worker/tools/registry.py`, controller ruling Q6) turns it into
    `unavailable("RuntimeError: tool raised")` before `evals.record.record` ever sees it —
    `record` must report this as a failure keyed by exception CLASS only; the message ("boom")
    must never reach a report or a fixture file.
    """

    name = "lookup_ip_reputation"
    description = "raises to exercise evals.record's failure-reporting path"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": ["ip"],
        "additionalProperties": False,
    }
    external = True

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        raise RuntimeError("boom - should never reach a report or a fixture file")


def _registry(fixtures_dir: Path, *, tools: list[Any] | None = None) -> ToolRegistry:
    """A `ToolRegistry` over `tools` (default: both canned external tools), wired with a
    `LiveToolRecorder(record_dir=fixtures_dir)` — `record`'s own doc says it "calls
    `registry.execute` under a `LiveToolRecorder(record_dir=fixtures_dir)`", i.e. the caller
    (here, the test; in production, `evals.record.main`) is the one that wires the registry this
    way, exactly like `worker/tools/wiring.py::build_registry` does for the real tools.
    """
    resolved_tools: list[Any] = tools or [
        _CannedExternalTool("get_ip_geo_asn"),
        _CannedExternalTool("lookup_ip_reputation"),
    ]
    return ToolRegistry(
        resolved_tools, recorder=LiveToolRecorder(record_dir=fixtures_dir), max_result_chars=10_000
    )


# --- planned_calls ------------------------------------------------------------------------------


def test_planned_calls_one_per_external_tool_per_src_ip_deduplicated() -> None:
    cases = [
        _case("alert1.json", src_ip="203.0.113.50"),
        _case("alert2.json", src_ip="203.0.113.60"),
        _case("alert3.json", src_ip="203.0.113.50"),  # shares an ip with the first case
    ]

    calls = planned_calls(cases)

    assert calls == [
        ("get_ip_geo_asn", {"ip": "203.0.113.50"}),
        ("get_ip_geo_asn", {"ip": "203.0.113.60"}),
        ("lookup_ip_reputation", {"ip": "203.0.113.50"}),
        ("lookup_ip_reputation", {"ip": "203.0.113.60"}),
    ]


# --- record: writes + idempotent skip -----------------------------------------------------------


async def test_record_writes_fixtures_via_live_recorder_and_skips_existing(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    cases = [
        _case("alert1.json", src_ip="203.0.113.50"),
        _case("alert2.json", src_ip="203.0.113.60"),
        _case("alert3.json", src_ip="203.0.113.50"),
    ]

    report = await record(
        cases, registry=_registry(fixtures_dir), fixtures_dir=fixtures_dir, only=None, dry_run=False
    )

    assert report.recorded == 4
    assert report.skipped_existing == 0
    assert report.failed == []
    for tool_name, arguments in planned_calls(cases):
        assert fixture_path(fixtures_dir, tool_name, arguments).exists()

    # Idempotent: a second run over the same cases/fixtures_dir writes nothing new.
    report2 = await record(
        cases, registry=_registry(fixtures_dir), fixtures_dir=fixtures_dir, only=None, dry_run=False
    )

    assert report2.recorded == 0
    assert report2.skipped_existing == 4
    assert report2.failed == []


# --- dry-run + failure reporting -----------------------------------------------------------------


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    cases = [_case("alert1.json", src_ip="203.0.113.70")]

    report = await record(
        cases, registry=_registry(fixtures_dir), fixtures_dir=fixtures_dir, only=None, dry_run=True
    )

    assert not fixtures_dir.exists() or list(fixtures_dir.rglob("*.json")) == []
    assert report.recorded == 0
    assert report.skipped_existing == 0
    assert report.failed == []
    assert report.planned == len(planned_calls(cases))

    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(cases[0].model_dump_json() + "\n")

    rc = main(
        ["--golden", str(golden_path), "--fixtures", str(fixtures_dir), "--dry-run"],
        registry=_registry(fixtures_dir),
    )

    assert rc == 0
    assert not fixtures_dir.exists() or list(fixtures_dir.rglob("*.json")) == []


def test_failed_tool_reported_by_class_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures_dir = tmp_path / "fixtures"
    ip = "203.0.113.80"
    cases = [_case("alert1.json", src_ip=ip)]
    tools: list[Any] = [_CannedExternalTool("get_ip_geo_asn"), _RaisingExternalTool()]

    report = asyncio.run(
        record(
            cases,
            registry=_registry(fixtures_dir, tools=tools),
            fixtures_dir=fixtures_dir,
            only=None,
            dry_run=False,
        )
    )

    key = fixture_key({"ip": ip})
    assert report.failed == [("lookup_ip_reputation", key, "RuntimeError")]
    assert report.recorded == 1  # get_ip_geo_asn succeeded independently of the raising tool
    assert not fixture_path(fixtures_dir, "lookup_ip_reputation", {"ip": ip}).exists()
    assert "boom" not in repr(report.failed)  # names/keys only, never the raised message

    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(cases[0].model_dump_json() + "\n")

    rc = main(
        ["--golden", str(golden_path), "--fixtures", str(fixtures_dir)],
        registry=_registry(fixtures_dir, tools=tools),
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "boom" not in captured.out
    assert "boom" not in captured.err
