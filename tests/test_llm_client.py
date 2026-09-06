"""Pins `core.llm` (Protocol + `parse_structured` + `compute_cost_usd`), the OpenAI-compatible
`worker.llm_client.OpenAICompatibleLLMClient`, and `tests.fakes.FakeLLMClient` (m0 task-03).

PRD §4 (LLM row), §6.5 (structured output), §10.1 (LLM calls only ever happen in `worker/`).
CONVENTIONS.md §2 contract 3 (`core.llm` carries no SDK import) and §10 (the fake validates
through the real `parse_structured` path so bad JSON fails identically in tests and production).
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import openai
import pytest

from core.config import ModelPrice, Settings
from core.errors import ConfigError, LLMCallError, StructuredOutputError
from core.llm import LLMResult, LLMUsage, compute_cost_usd, parse_structured
from core.schemas.verdict import Verdict
from tests.fakes import FakeCall, FakeLLMClient
from worker.llm_client import OpenAICompatibleLLMClient

# A valid Verdict reply body, shared across the tests that need one (task brief's fixed example).
_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials",
    "recommended_action": "monitor",
    "escalate": False,
}

_FAKE_PRICES = {
    "fake-model": ModelPrice(input_per_mtok=Decimal("0.15"), output_per_mtok=Decimal("0.60"))
}


def _chat_completion_body(content: str) -> dict[str, object]:
    """Build a minimal OpenAI-shaped chat-completion response body around `content`."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _mock_client(
    handler: httpx.MockTransport | None, *, max_retries: int = 2
) -> openai.AsyncOpenAI:
    """Build an `AsyncOpenAI` SDK client whose HTTP transport is fully mocked."""
    assert handler is not None
    return openai.AsyncOpenAI(
        api_key="test",
        base_url="http://test/v1",
        http_client=httpx.AsyncClient(transport=handler),
        max_retries=max_retries,
    )


def test_parse_structured_returns_model() -> None:
    raw_text = json.dumps(_VALID_VERDICT)
    usage = LLMUsage(100, 50)

    result = parse_structured(
        raw_text,
        Verdict,
        model="fake-model",
        usage=usage,
        cost_usd=Decimal("0.000123"),
        latency_ms=42,
    )

    assert isinstance(result, LLMResult)
    assert isinstance(result.parsed, Verdict)
    assert result.parsed.category == "brute_force"
    assert result.raw_text == raw_text
    assert result.model == "fake-model"
    assert result.usage == usage
    assert result.cost_usd == Decimal("0.000123")
    assert result.latency_ms == 42


def test_parse_structured_raises_with_raw_text_and_usage() -> None:
    raw_text = "{not json}"

    with pytest.raises(StructuredOutputError) as exc_info:
        parse_structured(
            raw_text,
            Verdict,
            model="fake-model",
            usage=LLMUsage(120, 40),
            cost_usd=Decimal("0.000123"),
            latency_ms=842,
        )

    err = exc_info.value
    assert err.raw_text == raw_text
    assert err.validation_error != ""
    assert err.input_tokens == 120
    assert err.output_tokens == 40
    assert err.cost_usd == Decimal("0.000123")
    assert err.latency_ms == 842


def test_compute_cost_usd_per_million_quantized() -> None:
    price = ModelPrice(input_per_mtok=Decimal("0.15"), output_per_mtok=Decimal("0.60"))
    usage = LLMUsage(100_000, 50_000)

    cost = compute_cost_usd(price, usage)

    assert cost == Decimal("0.045000")
    assert cost.as_tuple().exponent == -6


def test_from_settings_raises_when_cheap_model_empty() -> None:
    settings = Settings(cheap_model="", model_prices_json={})

    with pytest.raises(ConfigError):
        OpenAICompatibleLLMClient.from_settings(settings)


def test_from_settings_raises_when_model_unpriced() -> None:
    settings = Settings(cheap_model="m", model_prices_json={})

    with pytest.raises(ConfigError):
        OpenAICompatibleLLMClient.from_settings(settings)


async def test_complete_structured_sends_json_object_and_temperature_zero() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion_body(json.dumps(_VALID_VERDICT)))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    result = await client.complete_structured(
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "<<<ALERT_DATA>>>data<<<END_ALERT_DATA>>>"},
        ],
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(result.parsed, Verdict)
    assert result.usage == LLMUsage(100, 50)
    assert len(captured) == 1
    request_body = captured[0]
    assert request_body["temperature"] in (0, 0.0)
    assert request_body["response_format"] == {"type": "json_object"}
    assert request_body["model"] == "fake-model"


async def test_complete_structured_maps_http_500_to_llm_call_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler), max_retries=0),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(LLMCallError):
        await client.complete_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_model=Verdict,
            model="fake-model",
        )


async def test_missing_usage_raises_llm_call_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _chat_completion_body(json.dumps(_VALID_VERDICT))
        del body["usage"]
        return httpx.Response(200, json=body)

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler), max_retries=0),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(LLMCallError):
        await client.complete_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_model=Verdict,
            model="fake-model",
        )


async def test_fake_records_calls_and_replays() -> None:
    boom = LLMCallError("simulated failure")
    fake = FakeLLMClient([json.dumps(_VALID_VERDICT), boom])
    first_messages = [{"role": "user", "content": "first"}]

    result = await fake.complete_structured(
        messages=first_messages, response_model=Verdict, model="fake-model"
    )

    assert isinstance(result.parsed, Verdict)
    assert fake.calls == [
        FakeCall(messages=first_messages, response_model=Verdict, model="fake-model")
    ]

    with pytest.raises(LLMCallError) as exc_info:
        await fake.complete_structured(
            messages=[{"role": "user", "content": "second"}],
            response_model=Verdict,
            model="fake-model",
        )
    assert exc_info.value is boom
    assert len(fake.calls) == 2

    with pytest.raises(AssertionError, match="no responses left"):
        await fake.complete_structured(
            messages=[{"role": "user", "content": "third"}],
            response_model=Verdict,
            model="fake-model",
        )
    # Recorded even though the call had nothing left to replay — the call log is call-order
    # truth, independent of whether the reply could be produced.
    assert len(fake.calls) == 3
