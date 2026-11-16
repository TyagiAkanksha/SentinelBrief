"""Anthropic (Claude) async LLM client (PRD §4 LLM row, §6.3 tool calling, §6.5 structured output).

`AnthropicLLMClient` is the **only** module that imports the `anthropic` SDK (import-linter
contract "only worker.anthropic_llm_client imports anthropic", CONVENTIONS.md §2); every other
module programs against `core.llm.LLMClient`. It speaks the native Messages API — never
Anthropic's OpenAI-compatibility endpoint — and converts the provider-neutral, OpenAI-shaped
`ChatMessage`s to the Anthropic wire shape (top-level `system=`, `tool_use` / `tool_result`
blocks).

Structured output: the triage prompt already embeds the Verdict JSON schema and asks for a JSON
object, so this client relies on schema-in-prompt plus Pydantic validation via
`core.llm.parse_structured` (works on every model); it does not send `output_config.format`.
Claude may wrap that JSON in a ```json fence, which is stripped before validation.

Never sent: `temperature`/`top_p`/`top_k` (Haiku 5.5 rejects non-default values) and assistant
prefill. Thinking is adaptive and counts against `max_tokens`, so a `max_tokens` stop is a
fail-closed `LLMCallError`, as are a `refusal` and `model_context_window_exceeded`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from time import perf_counter
from typing import Any, Literal, cast

import anthropic
import httpx
from pydantic import BaseModel

from core.config import ModelPrice, Settings
from core.errors import ConfigError, LLMCallError
from core.llm import (
    ChatMessage,
    LLMResult,
    LLMUsage,
    ToolCallRequest,
    ToolCallTurn,
    ToolSpec,
    compute_cost_usd,
    parse_structured,
)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

_FENCE = "```"
_INFO_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _strip_fence(text: str) -> str:
    """Return the body of a single wrapping markdown code fence, else `text` unchanged.

    Plain string operations only (linear time): model output is steered by attacker-controlled
    alert text, so a backtracking regex here would be a worker-stalling hazard.
    """
    stripped = text.strip()
    if len(stripped) < 2 * len(_FENCE) or not (
        stripped.startswith(_FENCE) and stripped.endswith(_FENCE)
    ):
        return text
    inner = stripped[len(_FENCE) : -len(_FENCE)]
    start = 0
    while start < len(inner) and inner[start] in _INFO_CHARS:  # optional language tag
        start += 1
    body = inner[start:].lstrip()
    return body[:-1] if body.endswith("\n") else body


def _to_anthropic_messages(
    messages: Sequence[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert provider-neutral messages to (`system` text, Anthropic `messages` list).

    System messages are joined into the top-level system string; an assistant turn's
    `tool_calls` become `tool_use` blocks; adjacent user-role entries (`tool` results and
    plain `user` text) merge into ONE user message, `tool_result` blocks first (the API
    requires strict role alternation and results leading the turn).

    Raises:
        LLMCallError: A `tool` message has no `tool_call_id`, or history tool-call arguments
            are not a JSON object.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []

    def flush() -> None:
        if results or texts:
            blocks = [*results, *texts]
            plain = len(blocks) == 1 and not results
            out.append({"role": "user", "content": texts[0]["text"] if plain else blocks})
            results.clear()
            texts.clear()

    for message in messages:
        role = message["role"]
        if role == "system":
            system_parts.append(message["content"])
        elif role == "tool":
            tool_use_id = message.get("tool_call_id")
            if not tool_use_id:
                raise LLMCallError("tool message has no tool_call_id")
            results.append(
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": message["content"]}
            )
        elif role == "user":
            texts.append({"type": "text", "text": message["content"]})
        else:
            flush()
            if message.get("tool_calls"):
                out.append({"role": "assistant", "content": _assistant_blocks(message)})
            else:
                out.append({"role": role, "content": message["content"]})
    flush()
    return "\n\n".join(system_parts), out


def _assistant_blocks(message: ChatMessage) -> list[dict[str, Any]]:
    """Convert an assistant message carrying `tool_calls` to text + `tool_use` blocks."""
    blocks: list[dict[str, Any]] = []
    if message["content"]:
        blocks.append({"type": "text", "text": message["content"]})
    for call in message["tool_calls"]:
        blocks.append(
            {
                "type": "tool_use",
                "id": call["id"],
                "name": call["function"]["name"],
                "input": _decode_arguments(call["function"]["arguments"]),
            }
        )
    return blocks


def _decode_arguments(raw: str) -> dict[str, Any]:
    """Decode wire tool-call argument JSON text (empty text means no arguments)."""
    if not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMCallError("tool call arguments are not valid JSON") from e
    if not isinstance(decoded, dict):
        raise LLMCallError("tool call arguments are not a JSON object")
    return decoded


class AnthropicLLMClient:
    """Async LLM client backed by the `anthropic` SDK.

    Implements `core.llm.LLMClient` structurally (the Protocol is structural).
    """

    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic,
        prices: Mapping[str, ModelPrice],
        max_tokens: int,
        effort: Effort,
    ) -> None:
        """Wrap an already-constructed SDK client with prices and generation limits.

        Args:
            client: The `anthropic.AsyncAnthropic` client to issue message calls through.
            prices: Per-million-token USD prices, keyed by model id (PRD §6.4).
            max_tokens: Output cap per call (thinking tokens count against it).
            effort: Adaptive-thinking effort sent as `output_config.effort`.
        """
        self._client = client
        self._prices = prices
        self._max_tokens = max_tokens
        self._effort = effort

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicLLMClient:
        """Build a client from `Settings`, failing fast on missing or unpriced models.

        Args:
            settings: The application config surface.

        Returns:
            A ready-to-use `AnthropicLLMClient`.

        Raises:
            ConfigError: `settings.cheap_model` is empty, or `cheap_model`/a non-empty
                `strong_model` has no entry in `settings.model_prices_json`.
        """
        if not settings.cheap_model:
            raise ConfigError("CHEAP_MODEL is not set")
        for model in (settings.cheap_model, settings.strong_model):
            if model and model not in settings.model_prices_json:
                raise ConfigError(f"model {model!r} has no entry in MODEL_PRICES_JSON")
        client = anthropic.AsyncAnthropic(
            api_key=settings.llm_api_key.get_secret_value() or "unset",
            timeout=settings.llm_timeout_s,
            max_retries=2,
        )
        return cls(
            client=client,
            prices=settings.model_prices_json,
            max_tokens=settings.llm_max_tokens,
            effort=settings.llm_effort,
        )

    async def _send(
        self,
        *,
        messages: Sequence[ChatMessage],
        model: str,
        tool_specs: Sequence[ToolSpec] | None,
    ) -> tuple[Any, LLMUsage, Decimal, int]:
        """Shared request/usage/error path for both public methods.

        Prices `model` before any provider call (CONVENTIONS.md §7), sends the request, maps
        SDK/HTTP failures and fail-closed stop reasons to `LLMCallError`.

        Raises:
            ConfigError: `model` has no entry in the configured prices.
            LLMCallError: The call failed, stopped on `max_tokens`/`refusal`, or had no usage.
        """
        try:
            price = self._prices[model]
        except KeyError as e:
            raise ConfigError(f"model {model!r} has no entry in MODEL_PRICES_JSON") from e

        system, converted = _to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "messages": converted,
            "output_config": {"effort": self._effort},
        }
        if system:
            kwargs["system"] = system
        if tool_specs is not None:
            kwargs["tools"] = [
                {
                    "name": spec["name"],
                    "description": spec["description"],
                    "input_schema": spec["parameters"],
                }
                for spec in tool_specs
            ]
            kwargs["tool_choice"] = {"type": "auto"}

        start = perf_counter()
        try:
            response = await self._client.messages.create(**kwargs)
        except (anthropic.AnthropicError, httpx.HTTPError) as e:
            raise LLMCallError(f"LLM call failed: {e}") from e
        latency_ms = int((perf_counter() - start) * 1000)

        if response.stop_reason in ("max_tokens", "refusal", "model_context_window_exceeded"):
            raise LLMCallError(f"provider stopped with {response.stop_reason}")
        if response.usage is None:
            raise LLMCallError("provider returned no usage")
        usage = LLMUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return response, usage, compute_cost_usd(price, usage), latency_ms

    @staticmethod
    def _text_of(response: Any) -> str:
        """Concatenate the response's `text` blocks (thinking blocks are ignored)."""
        return "".join(b.text for b in response.content if b.type == "text")

    async def complete_structured[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T]:
        """Send `messages` to `model` and validate the reply as `response_model`.

        Raises:
            LLMCallError: The call failed, was cut off/refused, had no usage, or no text.
            ConfigError: `model` has no entry in the configured prices.
            StructuredOutputError: The reply text failed to validate as `response_model`.
        """
        response, usage, cost_usd, latency_ms = await self._send(
            messages=messages, model=model, tool_specs=None
        )
        return self._parse(response, response_model, model, usage, cost_usd, latency_ms)

    def _parse[T: BaseModel](
        self,
        response: Any,
        response_model: type[T],
        model: str,
        usage: LLMUsage,
        cost_usd: Decimal,
        latency_ms: int,
    ) -> LLMResult[T]:
        text = self._text_of(response)
        if not text.strip():
            raise LLMCallError("provider returned no content")
        return parse_structured(
            _strip_fence(text),
            response_model,
            model=model,
            usage=usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    async def complete_with_tools[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T] | ToolCallTurn:
        """Send `messages` to `model`, offering `tools` (PRD §6.3).

        Returns:
            A `ToolCallTurn` if the model asked for tools, else the validated `LLMResult`.

        Raises:
            ValueError: `tools` is empty.
            LLMCallError: The call failed, tool input was not an object, or no content.
            ConfigError: `model` has no entry in the configured prices.
            StructuredOutputError: A content reply failed to validate as `response_model`.
        """
        if not tools:
            raise ValueError("complete_with_tools requires at least one tool")
        response, usage, cost_usd, latency_ms = await self._send(
            messages=messages, model=model, tool_specs=tools
        )
        uses = [b for b in response.content if b.type == "tool_use"]
        if uses:
            calls = []
            for block in uses:
                if not isinstance(block.input, dict):
                    raise LLMCallError("tool call arguments are not a JSON object")
                calls.append(
                    ToolCallRequest(
                        id=block.id, name=block.name, arguments=cast(dict[str, Any], block.input)
                    )
                )
            return ToolCallTurn(
                calls=tuple(calls),
                model=model,
                usage=usage,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )
        return self._parse(response, response_model, model, usage, cost_usd, latency_ms)
