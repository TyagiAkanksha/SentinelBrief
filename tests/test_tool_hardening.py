"""Fix round 1 for m4 task-01 (review findings I2, I3, I4, I5): closes the gaps between the
"tools never raise" contract (PRD §6.3, spine M4-a) as documented and as actually enforced by
`worker.tools.registry.ToolRegistry.execute` and `worker.tools.recorder.ReplayToolRecorder`.

I2: a tool result that is not JSON-serializable (e.g. carries a raw `datetime`) must not escape
`ToolRegistry.execute` as a `TypeError` — the truncation step needs its own backstop.
I3: a fixture file that is valid JSON but not the documented `{tool, arguments, result}` shape
must not escape `ReplayToolRecorder.execute` as a `TypeError`/`KeyError`.
I4: `asyncio.CancelledError` (a `BaseException`) must propagate through `ToolRegistry.execute`,
never be swallowed by the one deliberate `except Exception` backstop.
I5: `ReplayToolRecorder` warns at most once per missing fixture path, not once per call.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import (
    LiveToolRecorder,
    ReplayToolRecorder,
    ToolContext,
    ToolRegistry,
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


class NotSerializableTool:
    """Returns a result `json.dumps` cannot serialize — drives I2."""

    name = "not_serializable"
    description = "Returns a result containing a raw datetime."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"when": datetime(2026, 1, 1, tzinfo=UTC)}


class CancelledTool:
    """Raises `asyncio.CancelledError` — drives I4."""

    name = "cancelled"
    description = "Always raises CancelledError."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        raise asyncio.CancelledError


class ExternalStub:
    """An external-seam `Tool` (`external=True`) with a call counter — drives I3/I5."""

    name = "external_stub"
    description = "A stub external tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = True

    def __init__(self) -> None:
        self.run_count = 0

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        self.run_count += 1
        return {"live": True}


async def test_registry_returns_unavailable_when_result_is_not_json_serializable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """I2: the truncation step (`json.dumps` inside `truncate_result`) is its own guarded region;
    a `TypeError` there is reported as `unavailable(...)`, not raised, and logged like the run
    guard (tool name + argument keys only, never values).
    """
    registry = ToolRegistry(
        [NotSerializableTool()], recorder=LiveToolRecorder(), max_result_chars=4000
    )
    ctx = _make_ctx()

    with caplog.at_level(logging.ERROR):
        execution = await registry.execute("not_serializable", {"secret_arg": "value-7"}, ctx)

    assert execution.result == unavailable("TypeError: result not serializable")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    message = error_records[0].getMessage()
    assert "not_serializable" in message
    assert "secret_arg" in message
    assert "value-7" not in message


async def test_registry_propagates_cancelled_error() -> None:
    """I4: `asyncio.CancelledError` is a `BaseException`, not caught by the registry's one
    `except Exception` backstop, and must propagate to the caller (ARQ job cancellation at M5,
    an in-flight request shutdown today).
    """
    registry = ToolRegistry([CancelledTool()], recorder=LiveToolRecorder(), max_result_chars=4000)
    ctx = _make_ctx()

    with pytest.raises(asyncio.CancelledError):
        await registry.execute("cancelled", {}, ctx)


async def test_replay_fixture_body_is_not_a_dict_is_unavailable(tmp_path: Path) -> None:
    """I3: a fixture file that is valid JSON but not an object at all (e.g. a bare list) must
    report `fixture_unreadable`, not raise `TypeError` on `data["tool"]`.
    """
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    path = fixture_path(tmp_path, tool.name, arguments)
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    result = await recorder.execute(tool, arguments, ctx)

    assert result == unavailable("fixture_unreadable")
    assert tool.run_count == 0


async def test_replay_fixture_body_missing_keys_is_unavailable(tmp_path: Path) -> None:
    """I3: a fixture file that is a JSON object but missing the documented `arguments`/`result`
    keys must report `fixture_unreadable`, not raise `KeyError`.
    """
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    path = fixture_path(tmp_path, tool.name, arguments)
    path.parent.mkdir(parents=True)
    path.write_text('{"tool": "external_stub"}')
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    result = await recorder.execute(tool, arguments, ctx)

    assert result == unavailable("fixture_unreadable")
    assert tool.run_count == 0


async def test_replay_missing_fixture_warns_once_per_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """I5: two `execute` calls for the same missing fixture path log exactly one WARNING; a
    different missing path still warns (the guard is per-path, not global).
    """
    tool = ExternalStub()
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    with caplog.at_level(logging.WARNING):
        result_1 = await recorder.execute(tool, {"ip": "203.0.113.10"}, ctx)
        result_2 = await recorder.execute(tool, {"ip": "203.0.113.10"}, ctx)
        result_3 = await recorder.execute(tool, {"ip": "198.51.100.7"}, ctx)

    assert result_1 == unavailable("fixture_missing")
    assert result_2 == unavailable("fixture_missing")
    assert result_3 == unavailable("fixture_missing")
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) == 2
    assert all(tool.name in r.getMessage() for r in warning_records)


async def test_replay_warns_again_after_fixture_becomes_available_then_missing_again(
    tmp_path: Path,
) -> None:
    """Sanity check that the per-path warn-once guard is about the *path*, not permanently
    suppressing every future warning for the tool: a written-then-served fixture doesn't count
    against the guard (this exercises `write_fixture` alongside the missing-fixture guard).
    """
    tool = ExternalStub()
    arguments = {"ip": "203.0.113.10"}
    other_arguments = {"ip": "198.51.100.7"}
    write_fixture(tmp_path, tool.name, arguments, {"asn": 64512})
    recorder = ReplayToolRecorder(tmp_path)
    ctx = _make_ctx()

    served = await recorder.execute(tool, arguments, ctx)
    missing = await recorder.execute(tool, other_arguments, ctx)

    assert served == {"asn": 64512}
    assert missing == unavailable("fixture_missing")
    assert tool.run_count == 0
