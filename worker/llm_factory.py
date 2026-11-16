"""The single place that picks an `LLMClient` implementation from `Settings.llm_provider`.

Every entry point (worker startup, `triage_one`, `seed_dev --live`, the eval harness) calls
`build_llm_client`; a source-scan test pins that nothing else calls either client's
`from_settings`. Provider and model ids must agree (`claude-*` iff the provider is anthropic) so a
mismatched `.env` fails at construction rather than on the first model call.
"""

from __future__ import annotations

from core.config import Settings
from core.errors import ConfigError
from core.llm import LLMClient
from worker.anthropic_llm_client import AnthropicLLMClient
from worker.llm_client import OpenAICompatibleLLMClient

_CLAUDE_PREFIX = "claude-"


def _check_model_matches_provider(setting: str, model: str, *, anthropic: bool) -> None:
    """Raise `ConfigError` when a non-empty model id disagrees with the provider."""
    if not model or model.startswith(_CLAUDE_PREFIX) == anthropic:
        return
    provider = "anthropic" if anthropic else "openai"
    expected = "must start with 'claude-'" if anthropic else "must not be a 'claude-' model"
    raise ConfigError(f"{setting}={model!r} does not match LLM_PROVIDER={provider}: {expected}")


def build_llm_client(settings: Settings) -> LLMClient:
    """Build the configured `LLMClient`.

    Args:
        settings: The application config surface.

    Returns:
        An `AnthropicLLMClient` when `llm_provider == "anthropic"`, else an
        `OpenAICompatibleLLMClient`.

    Raises:
        ConfigError: A model id disagrees with the provider (message names `CHEAP_MODEL` /
            `STRONG_MODEL`, never the key), or the chosen client rejects the settings.
    """
    anthropic = settings.llm_provider == "anthropic"
    _check_model_matches_provider("CHEAP_MODEL", settings.cheap_model, anthropic=anthropic)
    _check_model_matches_provider("STRONG_MODEL", settings.strong_model, anthropic=anthropic)
    if anthropic:
        return AnthropicLLMClient.from_settings(settings)
    return OpenAICompatibleLLMClient.from_settings(settings)
