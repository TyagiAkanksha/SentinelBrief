"""Live smoke test for `worker.llm_client.OpenAICompatibleLLMClient` (m0 task-03 / PRD §12 M0).

Marked `@pytest.mark.live` (CONVENTIONS.md §9/§10): excluded from the default `pytest -q` run and
from CI via `addopts = "-m 'not live'"`. Run it explicitly with `LLM_API_KEY`, `CHEAP_MODEL` and
`MODEL_PRICES_JSON` exported: `uv run pytest -m live tests/test_llm_live.py`. Never asserts on or
prints the API key.
"""

from __future__ import annotations

import json

import pytest
from worker.llm_client import OpenAICompatibleLLMClient

from core.config import Settings
from core.schemas.verdict import Verdict


@pytest.mark.live
async def test_live_complete_structured_returns_verdict() -> None:
    """A real endpoint returns a reply that validates as `Verdict` and reports real usage."""
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY not set")

    client = OpenAICompatibleLLMClient.from_settings(settings)
    schema = json.dumps(Verdict.model_json_schema())

    result = await client.complete_structured(
        messages=[
            {
                "role": "system",
                "content": (
                    "You triage security alerts. Reply with a single JSON object matching "
                    f"exactly this schema, no other text: {schema}"
                ),
            },
            {
                "role": "user",
                "content": (
                    "<<<ALERT_DATA>>>\n"
                    "One SSH login attempt from 203.0.113.5 succeeded on the first try using a "
                    "known-good key for an existing employee account during business hours.\n"
                    "<<<END_ALERT_DATA>>>"
                ),
            },
        ],
        response_model=Verdict,
        model=settings.cheap_model,
    )

    assert isinstance(result.parsed, Verdict)
    assert result.usage.input_tokens > 0
