"""The commented Anthropic example in `.env.example` must actually boot (final-review I3/C1).

Parses the example block, builds `Settings` from it, then runs the same construction chain
`worker.main.startup` uses (`build_llm_client` + `TriagePipeline.from_settings`), with no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.config import Settings
from worker.anthropic_llm_client import AnthropicLLMClient
from worker.llm_factory import build_llm_client
from worker.triage import TriagePipeline

_ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
_EXAMPLE_LINE = re.compile(r"^#\s{3}([A-Z][A-Z0-9_]*)=(.*)$")


def _example_env() -> dict[str, str]:
    """KEY=VALUE pairs from the three-space `#   KEY=VALUE` lines of the Anthropic example block."""
    env: dict[str, str] = {}
    in_block = False
    for line in _ENV_EXAMPLE.read_text().splitlines():
        if line.startswith("# Anthropic example"):
            in_block = True
            continue
        match = _EXAMPLE_LINE.match(line)
        if not in_block:
            continue
        if match is None:
            if env:  # first line after the block's KEY=VALUE run ends it
                break
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        env[match.group(1)] = value
    return env


@pytest.fixture
def example_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    env = _example_env()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LLM_API_KEY", "dummy-not-a-real-key")
    return Settings()


def test_example_block_sets_the_provider_and_both_tiers() -> None:
    env = _example_env()

    assert env["LLM_PROVIDER"] == "anthropic"
    assert env["CHEAP_MODEL"] == "claude-haiku-5-5"
    assert env["STRONG_MODEL"] == "claude-sonnet-5-5"


def test_example_prices_both_models(example_settings: Settings) -> None:
    prices = example_settings.model_prices_json

    assert {"claude-haiku-5-5", "claude-sonnet-5-5"} <= set(prices)


def test_example_builds_anthropic_client(example_settings: Settings) -> None:
    assert isinstance(build_llm_client(example_settings), AnthropicLLMClient)


def test_example_boots_the_triage_pipeline(example_settings: Settings) -> None:
    llm = build_llm_client(example_settings)

    pipeline = TriagePipeline.from_settings(example_settings, llm=llm)

    assert pipeline.tool_names
