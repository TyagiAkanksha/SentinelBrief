"""Live acceptance smoke for PRD §12 M4's two tool-loop clauses (m4 task-06):

- "a successful-login fixture triggers `get_session_commands`" (`alert4`)
- "a bare port scan completes with <= 1 tool call" (`alert1`)

Marked `@pytest.mark.live` (CONVENTIONS.md §9/§10): excluded from the default `pytest -q` run and
from CI via `addopts = "-m 'not live'"`. Run explicitly with `LLM_API_KEY` (and `CHEAP_MODEL` /
`MODEL_PRICES_JSON`) exported: `uv run pytest -m live tests/test_tool_loop_live.py`. External
tools replay from `tests/fixtures/tools/` even here (`ReplayToolRecorder`) — only the LLM call
itself is live (`.claude/rules/evals.md`). Never asserts on or prints the API key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import Settings
from tests.helpers import load_alert
from worker.llm_client import OpenAICompatibleLLMClient
from worker.tools import ReplayToolRecorder
from worker.triage import TriagePipeline

_TOOL_FIXTURES_DIR = Path("tests/fixtures/tools")


def _build_live_pipeline(settings: Settings) -> TriagePipeline:
    llm = OpenAICompatibleLLMClient.from_settings(settings)
    return TriagePipeline.from_settings(
        settings, llm=llm, recorder=ReplayToolRecorder(_TOOL_FIXTURES_DIR)
    )


@pytest.mark.live
async def test_live_successful_login_triggers_get_session_commands() -> None:
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY not set")

    pipeline = _build_live_pipeline(settings)
    alert = load_alert("alert4")

    outcome = await pipeline.run(alert)

    print("tool_names:", [record.tool_name for record in outcome.tool_calls])
    print(
        "input_tokens:",
        outcome.input_tokens,
        "output_tokens:",
        outcome.output_tokens,
        "cost_usd:",
        outcome.cost_usd,
    )
    assert any(record.tool_name == "get_session_commands" for record in outcome.tool_calls)


@pytest.mark.live
async def test_live_bare_scan_completes_with_at_most_one_tool_call() -> None:
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY not set")

    pipeline = _build_live_pipeline(settings)
    alert = load_alert("alert1")

    outcome = await pipeline.run(alert)

    print("tool_names:", [record.tool_name for record in outcome.tool_calls])
    print(
        "input_tokens:",
        outcome.input_tokens,
        "output_tokens:",
        outcome.output_tokens,
        "cost_usd:",
        outcome.cost_usd,
    )
    assert len(outcome.tool_calls) <= 1
