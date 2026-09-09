"""Pins `worker.tools`'s recorder seam: `fixture_key`/`fixture_path`/`write_fixture` (the fixture
format and its canonical key derivation), and `LiveToolRecorder` / `ReplayToolRecorder` (external
tools replay from `tests/fixtures/tools/<tool>/<key>.json`, local tools always run live, and a
missing/mismatched/unreadable fixture is reported, never raised) (m4 task-01).

PRD §7.2 (recorded tool-result fixtures for deterministic evals — never a live API in tests).
CONVENTIONS.md §10 (tool-calling tests replay recorded fixtures, never call a real external API).

Test tools (`ExternalStub`, a local stub) are tiny in-file `Tool` implementations with a call
counter — the external seam, not a mock of our own code (CONVENTIONS.md §10).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import (
    FIXTURE_KEY_CHARS,
    LiveToolRecorder,
    ReplayToolRecorder,
    ToolContext,
    fixture_key,
    fixture_path,
    unavailable,
    write_fixture,
)


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
    """An external-seam `Tool` (`external=True`) with a call counter — replay must never call
    it; live recording always does.
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


class LocalStub:
    """A local (`external=False`) `Tool` with a call counter — replay always runs it live."""

    name = "local_stub"
    description = "A stub local tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    def __init__(self) -> None:
        self.run_count = 0

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        self.run_count += 1
        return {"live": True}


def test_fixture_key_is_canonical_order_independent_and_16_hex() -> None:
    key1 = fixture_key({"a": 1, "b": 2})
    key2 = fixture_key({"b": 2, "a": 1})

    assert key1 == key2
    assert len(key1) == FIXTURE_KEY_CHARS
    assert all(c in "0123456789abcdef" for c in key1)
    assert fixture_key({"ip": "203.0.113.10"}) == "5d2e7bda8feb939e"


def test_fixture_path_layout(tmp_path: Path) -> None:
    arguments = {"ip": "203.0.113.10"}

    path = fixture_path(tmp_path, "get_ip_geo_asn", arguments)

    assert path == tmp_path / "get_ip_geo_asn" / f"{fixture_key(arguments)}.json"


async def test_replay_serves_the_recorded_result_for_an_external_tool(tmp_path: Path) -> None:
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    write_fixture(tmp_path, tool.name, arguments, {"asn": 64512})
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    result = await recorder.execute(tool, arguments, ctx)

    assert result == {"asn": 64512}
    assert tool.run_count == 0


async def test_replay_runs_a_non_external_tool_live(tmp_path: Path) -> None:
    tool = LocalStub()
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    result = await recorder.execute(tool, {}, ctx)

    assert result == {"live": True}
    assert tool.run_count == 1
    assert list(tmp_path.iterdir()) == []


async def test_replay_missing_fixture_is_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tool = ExternalStub()
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    with caplog.at_level(logging.WARNING):
        result = await recorder.execute(tool, {"ip": "203.0.113.10"}, ctx)

    assert result == unavailable("fixture_missing")
    assert tool.run_count == 0
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


async def test_replay_mismatched_fixture_is_unavailable(tmp_path: Path) -> None:
    tool = ExternalStub()
    correct_arguments = {"ip": "203.0.113.10"}
    other_arguments = {"ip": "198.51.100.7"}
    written_path = write_fixture(tmp_path, tool.name, other_arguments, {"asn": 1})
    target_path = fixture_path(tmp_path, tool.name, correct_arguments)
    written_path.rename(target_path)
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    result = await recorder.execute(tool, correct_arguments, ctx)

    assert result == unavailable("fixture_mismatch")
    assert tool.run_count == 0


async def test_replay_unreadable_fixture_is_unavailable(tmp_path: Path) -> None:
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    path = fixture_path(tmp_path, tool.name, arguments)
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    result = await recorder.execute(tool, arguments, ctx)

    assert result == unavailable("fixture_unreadable")
    assert tool.run_count == 0


async def test_live_recorder_executes_and_records_only_when_record_dir_is_set(
    tmp_path: Path,
) -> None:
    tool = ExternalStub()
    ctx = _make_ctx()

    recorder_no_dir = LiveToolRecorder()
    result = await recorder_no_dir.execute(tool, {"ip": "203.0.113.10"}, ctx)

    assert result == {"live": True}
    assert tool.run_count == 1
    assert list(tmp_path.iterdir()) == []

    record_dir = tmp_path / "recorded"
    recorder_with_dir = LiveToolRecorder(record_dir=record_dir)
    result2 = await recorder_with_dir.execute(tool, {"ip": "198.51.100.7"}, ctx)

    assert result2 == {"live": True}
    assert tool.run_count == 2
    fixture_file = fixture_path(record_dir, tool.name, {"ip": "198.51.100.7"})
    assert fixture_file.exists()
    data = json.loads(fixture_file.read_text())
    assert data == {
        "tool": tool.name,
        "arguments": {"ip": "198.51.100.7"},
        "result": {"live": True},
    }
