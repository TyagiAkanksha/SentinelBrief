"""Live smoke test for `evals.judge.judge_case` (m7 task-03).

Marked `@pytest.mark.live` (CONVENTIONS.md §9/§10): excluded from the default `pytest -q` run and
from CI via `addopts = "-m 'not live'"`. Run it explicitly with `LLM_API_KEY`, `STRONG_MODEL`
(falls back to `CHEAP_MODEL` when `STRONG_MODEL` is unset) and `MODEL_PRICES_JSON` (pricing
whichever model id is actually used) exported: `uv run pytest -m live tests/test_judge_live.py`.

Drives the real `OpenAICompatibleLLMClient` against the real, shipped `evals/prompts/judge-v1.md`
over one real `evals/golden/v1.jsonl` case's summary and a synthetic verdict — a smoke test of the
LLM seam end to end, not a scored claim (golden v1's numbers are never published,
`.claude/rules/evals.md`). Never prints the API key; prints only the resulting score.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.config import Settings
from core.schemas.verdict import Verdict
from evals.golden import load_golden
from evals.judge import judge_case
from worker.llm_client import OpenAICompatibleLLMClient
from worker.summarize import summarize_session

GOLDEN_V1 = Path("evals/golden/v1.jsonl")


@pytest.mark.live
def test_live_judge_scores_a_v1_case() -> None:
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY not set")

    model = settings.strong_model or settings.cheap_model
    if not model:
        pytest.skip("neither STRONG_MODEL nor CHEAP_MODEL set")

    client = OpenAICompatibleLLMClient.from_settings(settings)
    case = load_golden(GOLDEN_V1)[0]
    summary = summarize_session(case.alert)
    # A synthetic verdict consistent with the case's own label — this test smoke-tests the judge
    # seam end to end, it does not exercise a real triage pipeline output.
    verdict = Verdict(
        severity=case.label.severity,
        category=case.label.category,
        confidence=0.8,
        reasoning=(
            "the session shows repeated login attempts against generic usernames with no "
            "successful login, consistent with untargeted credential-spraying activity."
        ),
        recommended_action="monitor the source ip for continued brute-force activity.",
        escalate=case.label.escalate,
    )

    outcome = asyncio.run(
        judge_case(
            client,
            model=model,
            prompt_version=settings.judge_prompt_version,
            summary=summary,
            tool_results=[],
            verdict=verdict,
        )
    )

    assert 1 <= outcome.score.score <= 5
    print(f"live judge score: {outcome.score.score}")
