"""Pins core.config.Settings: zero-env construction, secret hiding, price decoding (m0 task-02).

CONVENTIONS.md §7: Settings() must construct with zero env vars, secrets never leak through
repr(), and MODEL_PRICES_JSON decodes to Decimal per model id.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from core.config import Settings

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
    assert settings.triage_prompt_version == "triage-v4"
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


# --- m0 final-review fix wave (t2 M2): additive only, no existing test/helper changed above. ---


def test_is_dev_false_for_production_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production branch was untested: mutation M-G (dropping `.strip().lower()`) passed
    every existing config test. Case/whitespace-insensitivity is the named `is_dev` behavior.
    """
    _clear_settings_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", " Production ")

    settings = Settings()

    assert settings.is_dev is False


# --- m6 task-02: the api's ingest body cap (PRD §6.1 step 1 / Global Constraint) ---


def test_ingest_max_body_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`INGEST_MAX_BODY_BYTES`'s `.env.example` default is 2,000,000 (Caddy's own 2 MB outer
    bound, task-03); the shipper's `SHIPPER_MAX_PAYLOAD_BYTES` must stay below it. At RED,
    `Settings` has no `ingest_max_body_bytes` field yet — this fails with `AttributeError`.
    """
    monkeypatch.delenv("INGEST_MAX_BODY_BYTES", raising=False)

    # R17: the .env.example default, literal on purpose
    assert Settings().ingest_max_body_bytes == 2_000_000


# --- m6 task-03 carried M5 item: LLM_TIMEOUT_S replaces worker/llm_client.py:94's literal ---


def test_llm_timeout_s_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LLM_TIMEOUT_S`'s `.env.example` default is 60 seconds — the same value
    `worker/llm_client.py:94` currently hardcodes as `timeout=60.0`, becoming
    `timeout=settings.llm_timeout_s` (CONVENTIONS.md §7). At RED, `Settings` has no
    `llm_timeout_s` field yet — this fails with `AttributeError`.
    """
    monkeypatch.delenv("LLM_TIMEOUT_S", raising=False)

    # R17: the .env.example default, literal on purpose
    assert Settings().llm_timeout_s == 60.0


# --- m7 task-05: the PRD §7.4 eval gate's three thresholds, parameterized (ruling R17) ------------


def test_eval_gate_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD §7.4's three gate thresholds are `Settings` fields with the PRD-literal defaults
    (`EVAL_GATE_SEVERITY_DROP_POINTS=3`, `EVAL_GATE_CRITICAL_RECALL_MIN=0.90`,
    `EVAL_GATE_COST_RISE_FRACTION=0.50`) -- never hardcoded in `evals/gate.py`
    (CONVENTIONS.md §7). At RED, `Settings` has none of these three fields yet -- every attribute
    access below fails with `AttributeError`.
    """
    monkeypatch.delenv("EVAL_GATE_SEVERITY_DROP_POINTS", raising=False)
    monkeypatch.delenv("EVAL_GATE_CRITICAL_RECALL_MIN", raising=False)
    monkeypatch.delenv("EVAL_GATE_COST_RISE_FRACTION", raising=False)

    settings = Settings()

    # R17: the .env.example defaults, literal on purpose
    assert settings.eval_gate_severity_drop_points == 3
    assert settings.eval_gate_critical_recall_min == pytest.approx(0.90)
    assert settings.eval_gate_cost_rise_fraction == pytest.approx(0.50)
