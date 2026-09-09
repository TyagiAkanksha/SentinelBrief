"""Pins `worker.tools`'s registry seam: `Tool`/`ToolContext` shapes, `spec_for`/`unavailable`,
`ToolRegistry` (duplicate-name/budget validation, registration-order `names`/`specs`, delegation
through an injected `ToolRecorder`, the injectable-clock latency seam, and the one backstop
`except Exception` around a raising tool), `truncate_result`'s per-tool character budget, and the
`tool_result_max_chars` setting (m4 task-01).

PRD §6.3 (a per-tool result budget is fed back to the model), §10.6 (tool results are
attacker-influenced data — a raising tool's argument *values* never reach the log, only its
keys). CONVENTIONS.md §4 (the registry's one `except Exception` is a ruled exception at the
worker's boundary with third-party/tool code — controller ruling Q6) and §7 (bounds are
`Settings`, never a literal).

Test tools (`EchoTool`, `BigTool`, `BoomTool`, plus two throwaway `_ToolA`/`_ToolB`) are tiny
in-file `Tool` implementations — the external seam, not a mock of our own code
(CONVENTIONS.md §10).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from core.config import Settings
from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import (
    Tool,
    ToolContext,
    ToolExecution,
    ToolRegistry,
    spec_for,
    truncate_result,
    unavailable,
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


class EchoTool:
    """Echoes its arguments back — the plain-path `Tool`."""

    name = "echo"
    description = "Echoes its arguments back."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return dict(arguments)


class BigTool:
    """Returns an oversized result — drives `ToolRegistry.execute`'s truncation."""

    name = "big"
    description = "Returns an oversized result."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"data": "x" * 10_000}


class BoomTool:
    """Always raises — drives `ToolRegistry.execute`'s backstop (controller ruling Q6)."""

    name = "boom"
    description = "Always raises."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        raise RuntimeError("boom")


class _ToolA:
    name = "a"
    description = "tool a"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {}


class _ToolB:
    name = "b"
    description = "tool b"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {}


class _LiveRecorder:
    """A minimal `ToolRecorder` that just calls `tool.run` — these tests exercise the
    registry's own delegation and backstop, not `LiveToolRecorder`/`ReplayToolRecorder`
    themselves (covered in `tests/test_tool_recorder.py`).
    """

    async def execute(
        self, tool: Tool, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        return await tool.run(arguments, ctx)


class _RecordingRecorder:
    """A `ToolRecorder` stub that records every `(tool, arguments, ctx)` it was called with and
    returns a fixed result — used to pin that the registry delegates through the recorder rather
    than calling `tool.run` itself.
    """

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.seen: list[tuple[Tool, dict[str, Any], ToolContext]] = []
        self._result = result if result is not None else {"ok": True}

    async def execute(
        self, tool: Tool, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        self.seen.append((tool, dict(arguments), ctx))
        return dict(self._result)


def test_spec_for_and_unavailable_shapes() -> None:
    tool = EchoTool()

    spec = spec_for(tool)

    assert set(spec.keys()) == {"name", "description", "parameters"}
    assert spec["name"] == "echo"
    assert spec["description"] == tool.description
    assert spec["parameters"] == tool.parameters

    assert unavailable("bad_ip") == {"unavailable": True, "reason": "bad_ip"}


def test_registry_rejects_duplicate_names_and_nonpositive_budget() -> None:
    with pytest.raises(ValueError):
        ToolRegistry([EchoTool(), EchoTool()], recorder=_LiveRecorder(), max_result_chars=100)

    with pytest.raises(ValueError):
        ToolRegistry([EchoTool()], recorder=_LiveRecorder(), max_result_chars=0)


def test_registry_names_and_specs_follow_registration_order() -> None:
    tool_b = _ToolB()
    tool_a = _ToolA()
    registry = ToolRegistry([tool_b, tool_a], recorder=_LiveRecorder(), max_result_chars=100)

    assert registry.names == ("b", "a")
    assert registry.specs() == [spec_for(tool_b), spec_for(tool_a)]


async def test_registry_executes_through_the_recorder_and_measures_latency() -> None:
    tool = EchoTool()
    recorder = _RecordingRecorder()
    clock_values = iter([1.0, 1.25])
    registry = ToolRegistry(
        [tool], recorder=recorder, max_result_chars=4000, clock=lambda: next(clock_values)
    )
    ctx = _make_ctx()
    arguments = {"k": "v"}

    execution = await registry.execute("echo", arguments, ctx)

    assert isinstance(execution, ToolExecution)
    assert execution.result == {"ok": True}
    assert execution.latency_ms == 250
    assert recorder.seen == [(tool, arguments, ctx)]


async def test_registry_unknown_tool_is_unavailable_and_never_raises() -> None:
    recorder = _RecordingRecorder()
    registry = ToolRegistry([EchoTool()], recorder=recorder, max_result_chars=4000)
    ctx = _make_ctx()

    execution = await registry.execute("nope", {}, ctx)

    assert execution.result == unavailable("unknown_tool")
    assert execution.latency_ms == 0
    assert recorder.seen == []


async def test_execute_returns_unavailable_when_a_tool_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock_values = iter([2.0, 2.01])
    registry = ToolRegistry(
        [BoomTool()],
        recorder=_LiveRecorder(),
        max_result_chars=4000,
        clock=lambda: next(clock_values),
    )
    ctx = _make_ctx()

    with caplog.at_level(logging.ERROR):
        execution = await registry.execute("boom", {"secret_arg": "value-7"}, ctx)

    assert execution.result == unavailable("RuntimeError: tool raised")
    assert execution.latency_ms == 10
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    message = error_records[0].getMessage()
    assert "boom" in message
    assert "secret_arg" in message
    assert "value-7" not in message


async def test_registry_truncates_oversized_results_to_the_budget() -> None:
    registry = ToolRegistry([BigTool()], recorder=_LiveRecorder(), max_result_chars=100)
    ctx = _make_ctx()

    execution = await registry.execute("big", {}, ctx)

    assert execution.result["truncated"] is True
    assert len(execution.result["preview"]) == 100
    assert execution.result["original_chars"] > 100


def test_truncate_result_under_budget_is_identity_and_over_budget_has_preview() -> None:
    small = {"a": 1}

    identity = truncate_result(small, 100)

    assert identity == small
    assert identity is not small

    big = {"data": "é" * 10_000}
    serialized = json.dumps(big, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    truncated = truncate_result(big, 100)

    assert set(truncated.keys()) == {"truncated", "original_chars", "preview"}
    assert truncated["truncated"] is True
    assert truncated["original_chars"] == len(serialized)
    assert truncated["preview"] == serialized[:100]
    assert "é" in truncated["preview"]
    assert "\\u00e9" not in truncated["preview"]


def test_tool_result_max_chars_setting_default_env_and_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().tool_result_max_chars == 4000

    monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "10")
    assert Settings().tool_result_max_chars == 10

    with pytest.raises(ValidationError):
        Settings(tool_result_max_chars=0)
