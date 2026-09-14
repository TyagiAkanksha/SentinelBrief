"""OpenAI-compatible async LLM client (PRD §4 LLM row, §6.3 tool calling, §6.5 structured output).

`OpenAICompatibleLLMClient` is the **only** module in this codebase that imports the `openai`
SDK (import-linter contract 3, CONVENTIONS.md §2); every other module programs against
`core.llm.LLMClient`. It speaks to any OpenAI-compatible endpoint (`LLM_BASE_URL`) at
`temperature=0` and asks for JSON-only replies, then validates them through
`core.llm.parse_structured` so a malformed reply fails identically here and in
`tests.fakes.FakeLLMClient`.

`complete_structured` and `complete_with_tools` (m4 task-01) share one private request/usage/error
helper (`_send`) so the two paths cannot drift: same price-before-spend check, same JSON-mode
`response_format`, same SDK/HTTP error mapping. They differ only in what they do with the reply
message (content-only vs. content-or-`tool_calls`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from time import perf_counter
from typing import Any, Literal, cast

import httpx
import openai
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam
from openai.types.chat.completion_create_params import ResponseFormat
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


class OpenAICompatibleLLMClient:
    """Async LLM client backed by the `openai` SDK, usable against any compatible endpoint.

    Implements `core.llm.LLMClient` structurally (no inheritance needed — the Protocol is
    structural).
    """

    def __init__(
        self,
        *,
        client: openai.AsyncOpenAI,
        prices: Mapping[str, ModelPrice],
        json_mode: Literal["json_object", "json_schema"],
    ) -> None:
        """Wrap an already-constructed SDK client with the prices and JSON mode to use.

        Args:
            client: The `openai.AsyncOpenAI` client to issue chat-completion calls through.
            prices: Per-million-token USD prices, keyed by model id (PRD §6.4).
            json_mode: Whether to request `json_object` or `json_schema` structured output.
        """
        self._client = client
        self._prices = prices
        self._json_mode = json_mode

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAICompatibleLLMClient:
        """Build a client from `Settings`, failing fast on missing or unpriced models.

        An unpriced configured model is a boot-time `ConfigError`, never a silent zero cost
        (CONVENTIONS.md §7).

        Args:
            settings: The application config surface.

        Returns:
            A ready-to-use `OpenAICompatibleLLMClient`.

        Raises:
            ConfigError: `settings.cheap_model` is empty, or `cheap_model`/a non-empty
                `strong_model` has no entry in `settings.model_prices_json`.
        """
        if not settings.cheap_model:
            raise ConfigError("CHEAP_MODEL is not set")
        for model in (settings.cheap_model, settings.strong_model):
            if model and model not in settings.model_prices_json:
                raise ConfigError(f"model {model!r} has no entry in MODEL_PRICES_JSON")
        client = openai.AsyncOpenAI(
            api_key=settings.llm_api_key.get_secret_value() or "unset",
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_s,
            max_retries=2,
        )
        return cls(
            client=client,
            prices=settings.model_prices_json,
            json_mode=settings.llm_json_mode,
        )

    async def _send[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        model: str,
        tool_specs: Sequence[ToolSpec] | None,
    ) -> tuple[openai.types.chat.ChatCompletion, LLMUsage, Decimal, int]:
        """Shared request/usage/error path for `complete_structured` and `complete_with_tools`.

        Builds the configured JSON `response_format`, prices `model` before any provider call
        (never after — CONVENTIONS.md §7), issues the chat-completion request (adding
        `tools`/`tool_choice="auto"` when `tool_specs` is given), and maps SDK/HTTP failures to
        `LLMCallError`. The two public methods share this so they cannot drift; each decodes
        `response.choices[0].message` itself, which is where content-only and
        content-or-`tool_calls` diverge.

        Args:
            messages: The chat messages to send, in order.
            response_model: The Pydantic model a content reply must validate against (only its
                name/schema are used here, for `json_schema` mode).
            model: The model id to call.
            tool_specs: The tool specs to offer, or `None` for a plain `complete_structured` call
                (no `tools`/`tool_choice` sent).

        Returns:
            The raw SDK `ChatCompletion`, its `LLMUsage`, the priced `cost_usd`, and the
            wall-clock `latency_ms`.

        Raises:
            LLMCallError: The HTTP/SDK call failed, or the provider returned no usage.
            ConfigError: `model` has no entry in the configured prices.
        """
        if self._json_mode == "json_object":
            response_format: dict[str, object] = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": response_model.model_json_schema(),
                    "strict": True,
                },
            }

        # `ChatMessage`/`response_format` are our provider-neutral shapes (core.llm, no SDK
        # import allowed there); they are structurally identical to the SDK's own TypedDicts for
        # the same wire JSON, so this cast is the boundary where provider-neutral becomes
        # SDK-specific — contract 3 only forbids the SDK import in core.llm, not here. `ToolSpec`
        # (also provider-neutral) is wrapped in the SDK's `{"type": "function", "function": ...}`
        # shape and cast at the same boundary.
        messages_param = cast(list[ChatCompletionMessageParam], list(messages))
        response_format_param = cast(ResponseFormat, response_format)

        # Resolved before any provider call: an unpriced model must never be billed
        # (CONVENTIONS.md §7) — a check that ran after `create()` would only prevent a *silent*
        # zero-cost accounting, not the spend itself.
        try:
            price = self._prices[model]
        except KeyError as e:
            raise ConfigError(f"model {model!r} has no entry in MODEL_PRICES_JSON") from e

        tools_param: list[ChatCompletionToolUnionParam] | openai.Omit = openai.omit
        tool_choice_param: Literal["auto"] | openai.Omit = openai.omit
        if tool_specs is not None:
            tools_param = cast(
                list[ChatCompletionToolUnionParam],
                [{"type": "function", "function": spec} for spec in tool_specs],
            )
            tool_choice_param = "auto"

        start = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages_param,
                temperature=0.0,
                response_format=response_format_param,
                tools=tools_param,
                tool_choice=tool_choice_param,
            )
        except (openai.OpenAIError, httpx.HTTPError) as e:
            raise LLMCallError(f"LLM call failed: {e}") from e
        latency_ms = int((perf_counter() - start) * 1000)

        # Guards both callers' `response.choices[0]` access below — an empty `choices` list is
        # neither an `openai.OpenAIError` nor an `httpx.HTTPError`, so it would otherwise escape
        # unmapped as an `IndexError` (m4 task-01 fix-1, M6).
        if not response.choices:
            raise LLMCallError("provider returned no choices")

        if response.usage is None:
            raise LLMCallError("provider returned no usage")
        usage = LLMUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
        cost_usd = compute_cost_usd(price, usage)
        return response, usage, cost_usd, latency_ms

    async def complete_structured[T: BaseModel](
        self,
        *,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        model: str,
    ) -> LLMResult[T]:
        """Send `messages` to `model` at temperature 0 and validate the reply as `response_model`.

        Args:
            messages: The chat messages to send, in order.
            response_model: The Pydantic model the reply must validate against.
            model: The model id to call.

        Returns:
            An `LLMResult` carrying the parsed model plus raw text, usage, cost and latency.

        Raises:
            LLMCallError: The HTTP/SDK call failed, the provider returned no usage, or the
                provider returned no content.
            ConfigError: `model` has no entry in the configured prices.
            StructuredOutputError: The reply text failed to validate as `response_model`.
        """
        response, usage, cost_usd, latency_ms = await self._send(
            messages=messages, response_model=response_model, model=model, tool_specs=None
        )

        content = response.choices[0].message.content
        if not content:
            raise LLMCallError("provider returned no content")

        return parse_structured(
            content,
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
        """Send `messages` to `model` at temperature 0, offering `tools` (PRD §6.3).

        Args:
            messages: The chat messages to send, in order.
            tools: The tool specs to offer the model. Must be non-empty.
            response_model: The Pydantic model a content reply must validate against.
            model: The model id to call.

        Returns:
            A `ToolCallTurn` if the model asked for one or more tools (decoded arguments,
            usage/cost/latency for this turn), else an `LLMResult` carrying the validated content
            reply.

        Raises:
            ValueError: `tools` is empty.
            LLMCallError: The HTTP/SDK call failed, the provider returned no usage, a tool call
                had a non-`function` type, its arguments were not a JSON object, or the provider
                returned no content (when the model didn't call a tool).
            ConfigError: `model` has no entry in the configured prices.
            StructuredOutputError: A content reply failed to validate as `response_model`.
        """
        if not tools:
            raise ValueError("complete_with_tools requires at least one tool")

        response, usage, cost_usd, latency_ms = await self._send(
            messages=messages, response_model=response_model, model=model, tool_specs=tools
        )

        message = response.choices[0].message
        if message.tool_calls:
            calls = []
            for tool_call in message.tool_calls:
                if tool_call.type != "function":
                    raise LLMCallError(f"unsupported tool call type: {tool_call.type}")
                raw_arguments = tool_call.function.arguments
                if not raw_arguments.strip():
                    # Some OpenAI-compatible servers send `""` (or whitespace-only) for a
                    # no-parameter tool call instead of the spec-correct `"{}"` (m4 fix-wave
                    # task-01 M5); treat it the same as an empty object rather than failing the
                    # whole alert over a known quirk.
                    arguments: dict[str, Any] = {}
                else:
                    try:
                        arguments = json.loads(raw_arguments)
                    except json.JSONDecodeError as e:
                        raise LLMCallError("tool call arguments are not a JSON object") from e
                    if not isinstance(arguments, dict):
                        raise LLMCallError("tool call arguments are not a JSON object")
                calls.append(
                    ToolCallRequest(
                        id=tool_call.id, name=tool_call.function.name, arguments=arguments
                    )
                )
            return ToolCallTurn(
                calls=tuple(calls),
                model=model,
                usage=usage,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )

        content = message.content
        if not content:
            raise LLMCallError("provider returned no content")

        return parse_structured(
            content,
            response_model,
            model=model,
            usage=usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
