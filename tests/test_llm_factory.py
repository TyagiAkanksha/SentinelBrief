"""Pins `worker.llm_factory.build_llm_client` (anthropic-haiku task 3).

The single place that picks an `LLMClient` implementation from `Settings.llm_provider`, plus the
ledger-M1 boot check: provider and model ids must agree (`claude-*` iff provider is anthropic),
so a mismatched `.env` fails at construction, not on the first call.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.config import Settings
from core.errors import ConfigError
from worker.anthropic_llm_client import AnthropicLLMClient
from worker.llm_client import OpenAICompatibleLLMClient
from worker.llm_factory import build_llm_client

_SECRET = "sk-test-SUPERSECRET-123"
_PRICE = {"input_per_mtok": "0.10", "output_per_mtok": "0.50"}


def _settings(provider: str, cheap: str, strong: str = "", **overrides: Any) -> Settings:
    models = [m for m in (cheap, strong) if m]
    base: dict[str, Any] = {
        "llm_provider": provider,
        "llm_api_key": _SECRET,
        "cheap_model": cheap,
        "strong_model": strong,
        "model_prices_json": {m: _PRICE for m in models},
    }
    base.update(overrides)
    return Settings(**base)


def test_anthropic_provider_builds_anthropic_client() -> None:
    client = build_llm_client(_settings("anthropic", "claude-haiku-5-5"))

    assert isinstance(client, AnthropicLLMClient)


def test_openai_provider_builds_openai_compatible_client() -> None:
    client = build_llm_client(_settings("openai", "gpt-5.4-mini"))

    assert isinstance(client, OpenAICompatibleLLMClient)


def test_default_provider_builds_openai_compatible_client() -> None:
    settings = Settings(
        cheap_model="gpt-5.4-mini",
        model_prices_json={"gpt-5.4-mini": _PRICE},
    )

    assert isinstance(build_llm_client(settings), OpenAICompatibleLLMClient)


def test_anthropic_with_both_claude_tiers_builds() -> None:
    settings = _settings("anthropic", "claude-haiku-5-5", "claude-sonnet-5-5")

    assert isinstance(build_llm_client(settings), AnthropicLLMClient)


def test_anthropic_with_non_claude_cheap_model_raises_naming_setting() -> None:
    with pytest.raises(ConfigError) as info:
        build_llm_client(_settings("anthropic", "gpt-5.4-mini"))

    assert "CHEAP_MODEL" in str(info.value).upper()
    assert _SECRET not in str(info.value)


def test_anthropic_with_non_claude_strong_model_raises_naming_setting() -> None:
    with pytest.raises(ConfigError) as info:
        build_llm_client(_settings("anthropic", "claude-haiku-5-5", "gpt-5.4"))

    assert "STRONG_MODEL" in str(info.value).upper()
    assert _SECRET not in str(info.value)


def test_anthropic_with_empty_strong_model_is_allowed() -> None:
    client = build_llm_client(_settings("anthropic", "claude-haiku-5-5", ""))

    assert isinstance(client, AnthropicLLMClient)


def test_openai_with_claude_cheap_model_raises() -> None:
    with pytest.raises(ConfigError) as info:
        build_llm_client(_settings("openai", "claude-haiku-5-5"))

    assert "CHEAP_MODEL" in str(info.value).upper()
    assert _SECRET not in str(info.value)


def test_openai_with_claude_strong_model_raises() -> None:
    with pytest.raises(ConfigError) as info:
        build_llm_client(_settings("openai", "gpt-5.4-mini", "claude-sonnet-5-5"))

    assert "STRONG_MODEL" in str(info.value).upper()
    assert _SECRET not in str(info.value)
