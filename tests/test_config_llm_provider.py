"""Pins the Anthropic-provider settings on core.config.Settings (anthropic-haiku task 1).

`LLM_PROVIDER` / `LLM_EFFORT` / `LLM_MAX_TOKENS` select and tune the LLM backend. Defaults keep
today's behavior (OpenAI-compatible), and `.env.example` must document all three (the roster test
enforces it once the fields exist; the explicit test here names the three variables).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
_NEW_ENV_VARS = ("LLM_PROVIDER", "LLM_EFFORT", "LLM_MAX_TOKENS")
_OTHER_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_JSON_MODE",
    "LLM_TIMEOUT_S",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "ENVIRONMENT",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every relevant env var so a developer's shell cannot leak into a default."""
    for name in (*_NEW_ENV_VARS, *_OTHER_ENV_VARS):
        monkeypatch.delenv(name, raising=False)


def test_llm_provider_defaults_to_openai() -> None:
    assert Settings().llm_provider == "openai"


def test_llm_provider_accepts_anthropic_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    assert Settings().llm_provider == "anthropic"


def test_llm_provider_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    with pytest.raises(ValidationError):
        Settings()


def test_llm_effort_defaults_to_low() -> None:
    assert Settings().llm_effort == "low"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_llm_effort_accepts_each_valid_value(monkeypatch: pytest.MonkeyPatch, effort: str) -> None:
    monkeypatch.setenv("LLM_EFFORT", effort)

    assert Settings().llm_effort == effort


@pytest.mark.parametrize("bad", ["extreme", "LOW", "", "minimal"])
def test_llm_effort_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("LLM_EFFORT", bad)

    with pytest.raises(ValidationError):
        Settings()


def test_llm_max_tokens_defaults_to_4096() -> None:
    assert Settings().llm_max_tokens == 4096


def test_llm_max_tokens_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")

    assert Settings().llm_max_tokens == 8192


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_llm_max_tokens_must_be_positive(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("LLM_MAX_TOKENS", bad)

    with pytest.raises(ValidationError):
        Settings()


def test_llm_max_tokens_rejects_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_TOKENS", "lots")

    with pytest.raises(ValidationError):
        Settings()


def test_existing_llm_defaults_unchanged() -> None:
    settings = Settings()

    assert settings.llm_api_key.get_secret_value() == ""
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_json_mode == "json_object"
    assert settings.llm_timeout_s == 60.0
    assert settings.cheap_model == ""
    assert settings.strong_model == ""
    assert settings.model_prices_json == {}
    assert settings.triage_prompt_version == "triage-v4"


def test_llm_api_key_stays_the_single_key_field_with_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-test-value")

    settings = Settings()

    assert settings.llm_api_key.get_secret_value() == "sk-ant-test-value"
    assert "sk-ant-test-value" not in repr(settings)


@pytest.mark.parametrize("name", _NEW_ENV_VARS)
def test_env_example_documents_new_llm_setting(name: str) -> None:
    lines = _ENV_EXAMPLE.read_text().splitlines()

    assert any(re.match(rf"^#? ?{name}=", line) for line in lines), (
        f"{name} is not documented in .env.example"
    )
