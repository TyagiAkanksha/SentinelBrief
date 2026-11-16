"""`OpenAICompatibleLLMClient.from_settings` must refuse `llm_provider == "anthropic"`
(anthropic-haiku task 2): never send an Anthropic key to an OpenAI base URL.

Needs the Task 1 `Settings.llm_provider` field.
"""

from __future__ import annotations

import pytest

from core.config import Settings
from core.errors import ConfigError
from worker.llm_client import OpenAICompatibleLLMClient

_SECRET = "sk-test-SUPERSECRET-123"


def _settings(provider: str) -> Settings:
    return Settings(
        llm_provider=provider,  # type: ignore[arg-type]
        llm_api_key=_SECRET,  # type: ignore[arg-type]
        cheap_model="m",
        model_prices_json={"m": {"input_per_mtok": "0.10", "output_per_mtok": "0.50"}},
    )


def test_openai_client_refuses_anthropic_provider() -> None:
    with pytest.raises(ConfigError) as info:
        OpenAICompatibleLLMClient.from_settings(_settings("anthropic"))

    assert _SECRET not in str(info.value)


def test_openai_client_still_builds_for_openai_provider() -> None:
    client = OpenAICompatibleLLMClient.from_settings(_settings("openai"))

    assert isinstance(client, OpenAICompatibleLLMClient)
