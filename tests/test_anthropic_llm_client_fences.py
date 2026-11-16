"""Claude may wrap the JSON reply in a markdown fence; the client strips it (haiku task 2)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from core.errors import StructuredOutputError
from core.schemas.verdict import Verdict
from tests.test_anthropic_llm_client import _FAKE_PRICES, _VALID_VERDICT, _FakeAnthropic
from worker.anthropic_llm_client import AnthropicLLMClient


async def _run(text: str) -> Any:
    reply = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    client = AnthropicLLMClient(
        client=_FakeAnthropic(reply),  # type: ignore[arg-type]
        prices=_FAKE_PRICES,
        max_tokens=100,
        effort="low",
    )
    return await client.complete_structured(
        messages=[{"role": "user", "content": "x"}], response_model=Verdict, model="fake-model"
    )


@pytest.mark.parametrize("fence", ["```json\n{}\n```", "```\n{}\n```", "  ```json\n{}\n```\n"])
async def test_fenced_json_is_parsed(fence: str) -> None:
    result = await _run(fence.replace("{}", json.dumps(_VALID_VERDICT)))
    assert result.parsed.category == "brute_force"


async def test_fenced_garbage_still_fails_validation() -> None:
    with pytest.raises(StructuredOutputError):
        await _run("```json\nnot json\n```")
