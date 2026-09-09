"""`ToolRegistry`: looks tools up by name, executes them through a `ToolRecorder`, and enforces
the one character-budget backstop every tool result is truncated to (PRD §6.3).

`ToolRegistry.execute` **never raises** — an unknown tool name, a tool (or recorder) that raises,
or a tool result that can't be JSON-serialized all become `unavailable(...)` instead (spine
constraint M4-a). Two guarded regions inside `execute` — one around running the tool, one around
truncating its result — are the deliberate `except Exception` backstops in this codebase
(controller ruling Q6, CONVENTIONS.md §4): inline triage (until M5) runs inside the ingest
request, so an exception escaping here would 500 the request and strand the alert `pending`. The
exception's message is logged; the attacker-influenced argument *values* never are — only the
argument keys (PRD §10.6).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.llm import ToolSpec
from worker.tools.base import Tool, ToolContext, spec_for, unavailable
from worker.tools.recorder import ToolRecorder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolExecution:
    """The outcome of one `ToolRegistry.execute` call."""

    # Already truncated — exactly what the model will see and what is persisted.
    result: dict[str, Any]
    latency_ms: int


def truncate_result(result: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    """Truncate a tool `result` to `max_chars` of its compact JSON serialization.

    Args:
        result: The tool's (untruncated) result.
        max_chars: The character budget (PRD §6.3; `Settings.tool_result_max_chars`).

    Returns:
        `dict(result)` unchanged if its compact serialization fits in `max_chars`; otherwise a
        replacement dict `{"truncated": True, "original_chars": ..., "preview": ...}` — a valid
        JSON object whose `preview` is plain text (the model sees the head of the payload, the
        trace stays valid JSONB).
    """
    serialized = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return dict(result)
    return {
        "truncated": True,
        "original_chars": len(serialized),
        "preview": serialized[:max_chars],
    }


class ToolRegistry:
    """Looks tools up by name, executes them through a `ToolRecorder`, truncates every result."""

    def __init__(
        self,
        tools: Sequence[Tool],
        *,
        recorder: ToolRecorder,
        max_result_chars: int,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Register `tools`, validating names and the result budget up front.

        Args:
            tools: The tools to register, in the order the model should see them.
            recorder: How to actually execute a tool (live, replayed, optionally recorded).
            max_result_chars: The per-result character budget (`truncate_result`'s `max_chars`).
            clock: A zero-arg callable returning the current time, used to measure `latency_ms`
                — the latency seam (CONVENTIONS.md §10), defaulting to `time.perf_counter`.

        Raises:
            ValueError: Two tools share a `name`, or `max_result_chars < 1`.
        """
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate tool name(s) in {names}")
        if max_result_chars < 1:
            raise ValueError("max_result_chars must be >= 1")
        self._tools = list(tools)
        self._by_name = {tool.name: tool for tool in self._tools}
        self._recorder = recorder
        self._max_result_chars = max_result_chars
        self._clock = clock

    @property
    def names(self) -> tuple[str, ...]:
        """Registered tool names, in registration order."""
        return tuple(tool.name for tool in self._tools)

    def specs(self) -> list[ToolSpec]:
        """The `ToolSpec` for every registered tool, in registration order."""
        return [spec_for(tool) for tool in self._tools]

    async def execute(
        self, name: str, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> ToolExecution:
        """Run the tool named `name` with `arguments`/`ctx`; never raises.

        Args:
            name: The tool name the model asked for.
            arguments: The decoded tool-call arguments.
            ctx: The session/time/DB context to run the tool against.

        Returns:
            A `ToolExecution` whose `result` is already truncated to `max_result_chars`.
            `unavailable("unknown_tool")` (latency 0, the recorder is never called) if `name`
            isn't registered; `unavailable("<ExceptionClass>: tool raised")` (with one ERROR log
            naming `name` and `arguments`' keys, never their values) if the recorder/tool raises;
            `unavailable("<ExceptionClass>: result not serializable")` (same log shape) if the
            result can't be JSON-serialized. `asyncio.CancelledError` is a `BaseException`, not
            caught by either guard, and propagates.
        """
        tool = self._by_name.get(name)
        if tool is None:
            return ToolExecution(result=unavailable("unknown_tool"), latency_ms=0)

        start = self._clock()
        try:
            result = await self._recorder.execute(tool, arguments, ctx)
        except Exception as exc:  # first of the two deliberate backstops (ruling Q6, spine M4-a)
            logger.exception("tool raised tool=%s arg_keys=%s", name, sorted(arguments))
            result = unavailable(f"{type(exc).__name__}: tool raised")
        # round(), not int(): a scripted clock (e.g. 2.0 -> 2.01) can land a hair under the exact
        # millisecond boundary in binary floating point (2.01 - 2.0 == 9.999999999999787), and a
        # truncating int() would report 9ms instead of the intended 10ms. Measured around the run
        # only — truncation is guarded separately below.
        latency_ms = round((self._clock() - start) * 1000)

        try:
            truncated = truncate_result(result, self._max_result_chars)
        except Exception as exc:
            # second of the two deliberate backstops: a result json.dumps can't serialize (e.g.
            # a raw datetime) must not escape either (spine constraint M4-a).
            logger.exception(
                "tool result not serializable tool=%s arg_keys=%s", name, sorted(arguments)
            )
            truncated = truncate_result(
                unavailable(f"{type(exc).__name__}: result not serializable"),
                self._max_result_chars,
            )
        return ToolExecution(result=truncated, latency_ms=latency_ms)
