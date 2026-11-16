"""Final-review fix round (anthropic-haiku task 5): I1 linear fence stripping, I2 key guard.

I1 seam: `AnthropicLLMClient.complete_structured` over the hand-rolled fake SDK (public path; the
private `_strip_fence` is deliberately not imported). I2: `OpenAICompatibleLLMClient.from_settings`.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from core.config import Settings
from core.errors import ConfigError, LLMCallError, StructuredOutputError
from core.schemas.verdict import Verdict
from tests.test_anthropic_llm_client import _FAKE_PRICES, _VALID_VERDICT, _FakeAnthropic
from worker.anthropic_llm_client import AnthropicLLMClient
from worker.llm_client import OpenAICompatibleLLMClient

_BIG = 200_000
_QUADRATIC_PROBE = 20_000  # reply = fence + N spaces + JSON, no closing fence (review I1)
_BUDGET_S = 1.0
_VERDICT_JSON = json.dumps(_VALID_VERDICT)


async def _run(text: str, stop_reason: str = "end_turn") -> Any:
    reply = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    client = AnthropicLLMClient(
        client=_FakeAnthropic(reply),  # type: ignore[arg-type]
        prices=_FAKE_PRICES,
        max_tokens=100,
        effort="low",
    )
    return await client.complete_structured(
        messages=[{"role": "user", "content": "x"}], response_model=Verdict, model="fake-model"
    )


async def _timed(text: str) -> float:
    """Seconds spent in `complete_structured`; a validation failure is fine, slowness is not."""
    start = time.perf_counter()
    try:
        await _run(text)
    except StructuredOutputError:
        pass
    return time.perf_counter() - start


# --- I1: linear-time fence stripping ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("```" + " " * _BIG, id="bare-fence-spaces-no-close"),
        pytest.param("```json" + " " * _BIG, id="json-fence-spaces-no-close"),
        pytest.param("```json" + "\n" * _BIG, id="json-fence-newlines-no-close"),
        pytest.param("```json" + " \n" * (_BIG // 2) + "```", id="json-fence-ws-closed"),
        pytest.param(
            "```json" + " " * _QUADRATIC_PROBE + _VERDICT_JSON, id="json-fence-spaces-then-json-20k"
        ),
    ],
)
async def test_fence_stripping_is_linear_time(text: str) -> None:
    assert await _timed(text) < _BUDGET_S


async def test_big_whitespace_inside_valid_fence_still_parses_quickly() -> None:
    text = "```json\n" + " " * _BIG + _VERDICT_JSON + "\n```"
    start = time.perf_counter()
    result = await _run(text)
    assert time.perf_counter() - start < _BUDGET_S
    assert result.parsed.category == "brute_force"


@pytest.mark.parametrize("fence", ["```json\n{}\n```", "```\n{}\n```", "  ```json\n{}\n```\n"])
async def test_fenced_json_still_parses(fence: str) -> None:
    result = await _run(fence.replace("{}", _VERDICT_JSON))
    assert result.parsed.category == "brute_force"


async def test_fenced_garbage_still_fails_validation() -> None:
    with pytest.raises(StructuredOutputError):
        await _run("```json\nnot json\n```")


# --- Minor: fail-closed stops survive any refactor -------------------------------------------


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "model_context_window_exceeded"])
async def test_fail_closed_stop_reasons_still_raise(stop: str) -> None:
    with pytest.raises(LLMCallError):
        await _run(_VERDICT_JSON, stop_reason=stop)


# --- I2: an Anthropic key never reaches an OpenAI-compatible URL -----------------------------

_ANT_KEY = "sk-ant-api03-ZZtopSecretTail9876"


def _settings(key: str, provider: str) -> Settings:
    return Settings(
        llm_provider=provider,  # type: ignore[arg-type]
        llm_api_key=key,  # type: ignore[arg-type]
        cheap_model="m",
        model_prices_json={"m": {"input_per_mtok": "0.10", "output_per_mtok": "0.50"}},
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_openai_client_refuses_anthropic_key_any_provider(provider: str) -> None:
    with pytest.raises(ConfigError) as info:
        OpenAICompatibleLLMClient.from_settings(_settings(_ANT_KEY, provider))

    message = str(info.value)
    assert _ANT_KEY not in message
    assert _ANT_KEY[len("sk-ant-") :] not in message
    assert "ZZtop" not in message


def test_openai_client_still_builds_with_normal_key() -> None:
    client = OpenAICompatibleLLMClient.from_settings(_settings("sk-proj-normal-key-1", "openai"))

    assert isinstance(client, OpenAICompatibleLLMClient)
