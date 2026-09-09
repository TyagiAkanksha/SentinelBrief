"""LLMClient Protocol + result/usage types (CONVENTIONS.md §2, PRD §4/§6.3/§6.5/§10.1).

This module is the provider-neutral seam between `worker/` and any LLM backend: a `Protocol`,
plain dataclasses, and two pure helpers (`parse_structured`, `compute_cost_usd`). It carries **no
SDK import** — `worker/llm_client.py` is the only module that imports `openai` (import-linter
contract 3). `tests/fakes.py::FakeLLMClient` replays canned replies through `parse_structured` so
a malformed reply fails identically in tests and production (CONVENTIONS.md §10).

m4 task-01 adds the tool-calling seam (PRD §6.3): `LLMClient.complete_with_tools` returns either a
`ToolCallTurn` (the model asked for tools) or the usual `LLMResult[T]` (the model answered) — one
Protocol, not a second one, so every `llm=…` seam keeps its type.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, ValidationError

from core.config import ModelPrice
from core.errors import StructuredOutputError


class ToolCallFunctionWire(TypedDict):
    """The `function` member of one wire-format tool call (openai SDK 3.8.0 shape)."""

    name: str
    arguments: str  # JSON text — exactly what the provider sends/expects


class ToolCallWire(TypedDict):
    """One `choices[0].message.tool_calls[i]` member, provider-neutral wire shape."""

    id: str
    type: Literal["function"]
    function: ToolCallFunctionWire


class ChatMessage(TypedDict):
    """One chat message sent to an LLM (PRD §4/§6.5).

    `tool_calls` appears on an assistant turn that requested tools; `tool_call_id` appears on a
    tool-result turn (echoes the `ToolCallRequest.id` it answers). Both are `NotRequired` so every
    existing `{"role": ..., "content": ...}` literal stays valid.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: NotRequired[list[ToolCallWire]]
    tool_call_id: NotRequired[str]


class ToolSpec(TypedDict):
    """A provider-neutral tool definition offered to the model (PRD §6.3)."""

    name: str
    description: str
    parameters: dict[str, Any]  # a JSON Schema object


@dataclass(frozen=True)
class LLMUsage:
    """Token counts consumed by one LLM call, used for cost accounting (PRD §6.4)."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    """The validated outcome of one structured-output LLM call (PRD §6.5)."""

    parsed: T
    raw_text: str
    model: str
    usage: LLMUsage
    cost_usd: Decimal
    latency_ms: int


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool call the model asked for, with arguments already decoded from wire JSON."""

    id: str
    name: str
    arguments: dict[str, Any]  # the decoded JSON object


@dataclass(frozen=True)
class ToolCallTurn:
    """ "The model asked for tools" — one or more `ToolCallRequest`s plus this turn's usage/cost."""

    calls: tuple[ToolCallRequest, ...]  # len >= 1
    model: str
    usage: LLMUsage
    cost_usd: Decimal
    latency_ms: int


def tool_calls_message(calls: Sequence[ToolCallRequest]) -> ChatMessage:
    """Build the assistant turn a caller appends before the tool-result turns.

    Built here, not at each call site, so the real client, `FakeLLMClient`, and the pipeline
    agree on one wire shape for re-feeding tool calls back to the model.

    Args:
        calls: The tool calls the model requested, in the order to echo them back.

    Returns:
        A `ChatMessage` with `role="assistant"`, empty `content`, and one `tool_calls` entry per
        `call` (arguments re-encoded as sorted-key JSON text, matching the provider's own wire
        format).
    """
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, sort_keys=True),
                },
            }
            for call in calls
        ],
    }


class LLMClient(Protocol):
    """Provider-neutral seam every LLM backend (real or fake) implements.

    `worker.llm_client.OpenAICompatibleLLMClient` and `tests.fakes.FakeLLMClient` are the two
    implementations; callers (e.g. `worker.triage.TriagePipeline`) only ever depend on this
    Protocol, never on a concrete SDK type (PRD §10.1).
    """

    async def complete_structured[T: BaseModel](
        self, *, messages: Sequence[ChatMessage], response_model: type[T], model: str
    ) -> LLMResult[T]:
        """Send `messages` to `model` and validate the reply as `response_model`.

        Args:
            messages: The chat messages to send, in order.
            response_model: The Pydantic model the reply must validate against.
            model: The model id to call.

        Returns:
            An `LLMResult` carrying the parsed model plus raw text, usage, cost and latency.

        Raises:
            LLMCallError: The underlying call failed (network, HTTP error, missing usage/content).
            StructuredOutputError: The reply text failed to validate as `response_model`.
        """
        ...

    async def complete_with_tools[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T] | ToolCallTurn:
        """Send `messages` to `model` offering `tools`; the model may call a tool or answer.

        The model decides which tools (if any) to call (PRD §6.3); the caller (the tool loop,
        task-06) executes them and feeds results back through further `complete_with_tools`
        calls until the model answers with content instead of `tool_calls`. A content reply is
        still the `Verdict` JSON object (PRD §6.5) and is validated exactly like
        `complete_structured`.

        Args:
            messages: The chat messages to send, in order.
            tools: The tool specs to offer the model. Must be non-empty — callers only ask for
                tools when a registry has some.
            response_model: The Pydantic model a content reply must validate against.
            model: The model id to call.

        Returns:
            A `ToolCallTurn` if the model asked for one or more tools, else an `LLMResult`
            carrying the validated content reply plus usage, cost and latency.

        Raises:
            ValueError: `tools` is empty.
            LLMCallError: The underlying call failed (network, HTTP error, missing usage/content,
                a non-`function` tool call type, or non-object tool call arguments).
            ConfigError: `model` has no entry in the configured prices.
            StructuredOutputError: A content reply failed to validate as `response_model`.
        """
        ...


def parse_structured[T: BaseModel](
    raw_text: str,
    response_model: type[T],
    *,
    model: str,
    usage: LLMUsage,
    cost_usd: Decimal,
    latency_ms: int,
) -> LLMResult[T]:
    """Validate `raw_text` as `response_model`, carrying usage/cost/latency through either path.

    Both the real client and `FakeLLMClient` call this so a malformed reply raises the identical
    `StructuredOutputError` in tests and production (CONVENTIONS.md §10).

    Args:
        raw_text: The raw LLM reply text (expected to be a single JSON object).
        response_model: The Pydantic model the reply must validate against.
        model: The model id that produced `raw_text`.
        usage: Token usage for the call that produced `raw_text`.
        cost_usd: Cost in USD of the call that produced `raw_text`.
        latency_ms: Wall-clock latency in milliseconds of the call that produced `raw_text`.

    Returns:
        An `LLMResult` wrapping the validated model and the call's metadata.

    Raises:
        StructuredOutputError: `raw_text` fails to validate as `response_model`; carries
            `raw_text`, the validation error, and the same usage/cost/latency for diagnosis and
            billing of the failed call.
    """
    try:
        parsed = response_model.model_validate_json(raw_text)
    except ValidationError as err:
        raise StructuredOutputError(
            f"{response_model.__name__} failed validation",
            raw_text=raw_text,
            validation_error=str(err),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        ) from err
    return LLMResult(
        parsed=parsed,
        raw_text=raw_text,
        model=model,
        usage=usage,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def compute_cost_usd(price: ModelPrice, usage: LLMUsage) -> Decimal:
    """Compute the USD cost of `usage` at `price`, quantized to 6 decimal places.

    Uses `Decimal` throughout (never `float`) to avoid precision drift in billing math
    (CONVENTIONS.md §7).

    Args:
        price: Per-million-token USD prices for the model that produced `usage`.
        usage: Token counts to price.

    Returns:
        The cost in USD, quantized to `Decimal("0.000001")` with half-up rounding.
    """
    raw_cost = (
        Decimal(usage.input_tokens) * price.input_per_mtok
        + Decimal(usage.output_tokens) * price.output_per_mtok
    ) / Decimal(1_000_000)
    return raw_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
