"""`FakeLLMClient`: the only LLM double in the suite (CONVENTIONS.md §10, m0 task-03).

Replays queued responses (`str`, `Exception`, or a `ScriptedToolCall` sequence) through the real
`core.llm.parse_structured` path so a bad JSON reply raises `StructuredOutputError` identically to
the real `worker.llm_client.OpenAICompatibleLLMClient`. Never hand-roll parsing here — that would
let the fake diverge from production behavior, which is exactly what CONVENTIONS.md §10 forbids.

m4 task-01 adds scripted tool-call turns (`ScriptedToolCall`, `FakeReply`,
`FakeLLMClient.complete_with_tools`) so the tool loop (task-06) and every tool (tasks 02-05) can
be driven without a network, and `FakeCall.tools` records the specs a `complete_with_tools` call
was offered.

RED-safety: this file is imported by most of the suite, so it must stay importable before
`core.llm` grows `ToolCallRequest`/`ToolCallTurn`/`ToolSpec`. Those three names are imported from
`core.llm` only under `TYPE_CHECKING` for annotations, and locally inside `complete_with_tools`
for the runtime constructor — that import runs only when a test actually calls the method, so a
RED commit fails only the tests that exercise tool calls, never collection of the whole suite.
This pattern stays in place after GREEN (the file is pinned).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from core.llm import ChatMessage, LLMResult, LLMUsage, parse_structured

if TYPE_CHECKING:
    from core.llm import ToolCallTurn, ToolSpec


@dataclass(frozen=True)
class ScriptedToolCall:
    """One scripted tool call a `FakeLLMClient` reply mints into a `ToolCallTurn` (m4 task-01).

    `id`: when left `""`, the fake mints `f"call_{turn}_{index}"` (both 1-based: `turn` is this
    reply's position in `fake.calls`, `index` is this call's position within the turn) so ids are
    deterministic across scripted turns without every test having to invent its own.
    """

    name: str
    arguments: dict[str, Any]
    id: str = ""


# `str` -> validated through `parse_structured` (unchanged); `Exception` -> raised;
# a sequence of `ScriptedToolCall` -> a `ToolCallTurn`. A `str` is also a `Sequence[str]`, so
# every dispatch below checks `isinstance(reply, str)` before treating a reply as a
# `Sequence[ScriptedToolCall]`.
FakeReply = str | Exception | Sequence[ScriptedToolCall]


@dataclass
class FakeCall:
    """One recorded call to `FakeLLMClient.complete_structured` or `.complete_with_tools`.

    `tools` is `None` for a `complete_structured` call and the offered specs (as a list, in
    order) for a `complete_with_tools` call.
    """

    messages: list[ChatMessage]
    response_model: type[BaseModel]
    model: str
    tools: list[ToolSpec] | None = None


class FakeLLMClient:
    """Test double for `core.llm.LLMClient`; replays canned replies in call order.

    Args:
        responses: Queue of replies. A `str` is validated through `parse_structured` (so bad
            JSON raises `StructuredOutputError` exactly like production); an `Exception`
            instance is raised as-is (so callers can simulate `LLMCallError`, etc.); a sequence
            of `ScriptedToolCall` is only valid for `complete_with_tools` and becomes a
            `ToolCallTurn`.
        usage: Token usage attached to every successful reply.
        cost_usd: Cost attached to every successful reply.
        latency_ms: Latency attached to every successful reply.
    """

    def __init__(
        self,
        responses: Sequence[FakeReply],
        *,
        usage: LLMUsage = LLMUsage(100, 50),  # noqa: B008 -- frozen dataclass, effectively immutable
        cost_usd: Decimal = Decimal("0.000100"),
        latency_ms: int = 5,
    ) -> None:
        """Queue the canned responses and record the fixed usage/cost/latency to attach."""
        self._responses: list[FakeReply] = list(responses)
        self._usage = usage
        self._cost_usd = cost_usd
        self._latency_ms = latency_ms
        self.calls: list[FakeCall] = []

    async def complete_structured[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T]:
        """Record the call, then pop and replay the next queued response.

        Raises:
            AssertionError: The response queue is exhausted, or the next queued reply is a
                scripted tool-call turn (those are only valid for `complete_with_tools`).
        """
        self.calls.append(
            FakeCall(messages=list(messages), response_model=response_model, model=model)
        )
        if not self._responses:
            raise AssertionError("FakeLLMClient: no responses left")
        next_response = self._responses.pop(0)
        if isinstance(next_response, str):
            return parse_structured(
                next_response,
                response_model,
                model=model,
                usage=self._usage,
                cost_usd=self._cost_usd,
                latency_ms=self._latency_ms,
            )
        if isinstance(next_response, Exception):
            raise next_response
        raise AssertionError(
            "FakeLLMClient: tool calls were scripted but complete_structured was called"
        )

    async def complete_with_tools[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T] | ToolCallTurn:
        """Record the call (including the offered `tools`), then pop and replay the next reply.

        Raises:
            AssertionError: The response queue is exhausted.
        """
        from core.llm import ToolCallRequest, ToolCallTurn

        self.calls.append(
            FakeCall(
                messages=list(messages),
                response_model=response_model,
                model=model,
                tools=list(tools),
            )
        )
        if not self._responses:
            raise AssertionError("FakeLLMClient: no responses left")
        next_response = self._responses.pop(0)
        if isinstance(next_response, str):
            return parse_structured(
                next_response,
                response_model,
                model=model,
                usage=self._usage,
                cost_usd=self._cost_usd,
                latency_ms=self._latency_ms,
            )
        if isinstance(next_response, Exception):
            raise next_response
        turn = len(self.calls)  # this reply's 1-based position in fake.calls
        calls = tuple(
            ToolCallRequest(
                id=scripted.id or f"call_{turn}_{index}",
                name=scripted.name,
                arguments=scripted.arguments,
            )
            for index, scripted in enumerate(next_response, start=1)
        )
        return ToolCallTurn(
            calls=calls,
            model=model,
            usage=self._usage,
            cost_usd=self._cost_usd,
            latency_ms=self._latency_ms,
        )
