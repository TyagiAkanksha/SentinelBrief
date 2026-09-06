"""OpenAI-compatible async LLM client (PRD §4 LLM row, §6.5 structured output).

`OpenAICompatibleLLMClient` is the **only** module in this codebase that imports the `openai`
SDK (import-linter contract 3, CONVENTIONS.md §2); every other module programs against
`core.llm.LLMClient`. It speaks to any OpenAI-compatible endpoint (`LLM_BASE_URL`) at
`temperature=0` and asks for JSON-only replies, then validates them through
`core.llm.parse_structured` so a malformed reply fails identically here and in
`tests.fakes.FakeLLMClient`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import perf_counter
from typing import Literal, cast

import httpx
import openai
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat.completion_create_params import ResponseFormat
from pydantic import BaseModel

from core.config import ModelPrice, Settings
from core.errors import ConfigError, LLMCallError
from core.llm import ChatMessage, LLMResult, LLMUsage, compute_cost_usd, parse_structured


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
            timeout=60.0,
            max_retries=2,
        )
        return cls(
            client=client,
            prices=settings.model_prices_json,
            json_mode=settings.llm_json_mode,
        )

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
        # SDK-specific — contract 3 only forbids the SDK import in core.llm, not here.
        messages_param = cast(list[ChatCompletionMessageParam], list(messages))
        response_format_param = cast(ResponseFormat, response_format)

        start = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages_param,
                temperature=0.0,
                response_format=response_format_param,
            )
        except (openai.OpenAIError, httpx.HTTPError) as e:
            raise LLMCallError(f"LLM call failed: {e}") from e
        latency_ms = int((perf_counter() - start) * 1000)

        if response.usage is None:
            raise LLMCallError("provider returned no usage")
        usage = LLMUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

        content = response.choices[0].message.content
        if not content:
            raise LLMCallError("provider returned no content")

        try:
            price = self._prices[model]
        except KeyError as e:
            raise ConfigError(f"model {model!r} has no entry in MODEL_PRICES_JSON") from e
        cost_usd = compute_cost_usd(price, usage)

        return parse_structured(
            content,
            response_model,
            model=model,
            usage=usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
