"""Pins core.config.Settings: zero-env construction, secret hiding, price decoding (m0 task-02).

CONVENTIONS.md §7: Settings() must construct with zero env vars, secrets never leak through
repr(), and MODEL_PRICES_JSON decodes to Decimal per model id.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from core.config import Settings
from pydantic import SecretStr, ValidationError

# Every env var CONVENTIONS.md §7 / .env.example maps to a Settings field for M0.
_SETTINGS_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_JSON_MODE",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "ENVIRONMENT",
)


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every Settings-mapped env var so a developer's shell cannot leak into the test."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_settings_constructs_with_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings()

    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_json_mode == "json_object"
    assert settings.cheap_model == ""
    assert settings.strong_model == ""
    assert settings.model_prices_json == {}
    assert settings.triage_prompt_version == "triage-v1"
    assert settings.environment == "development"


def test_secret_fields_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings(llm_api_key=SecretStr("sk-live-123"))

    assert "sk-live-123" not in repr(settings)


def test_model_prices_json_parses_to_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"m":{"input_per_mtok":0.15,"output_per_mtok":0.6}}',
    )

    settings = Settings()

    assert settings.model_prices_json["m"].input_per_mtok == Decimal("0.15")
    assert settings.model_prices_json["m"].output_per_mtok == Decimal("0.6")


def test_model_prices_json_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("MODEL_PRICES_JSON", "not-json")

    with pytest.raises(ValidationError):
        Settings()


def test_is_dev_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings()

    assert settings.is_dev is True
