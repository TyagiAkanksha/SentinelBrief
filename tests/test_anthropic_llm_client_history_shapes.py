"""Pins history-shape handling in `worker.anthropic_llm_client` (anthropic-haiku task 4 fix round).

Covers review findings C1/I2/I3 and M1-M3 (`.superpowers/sdd/anthropic-haiku/2-review.md`). Live
check by the controller (claude-haiku-5-5): a tool-less request whose history holds
tool_use/tool_result blocks is accepted, and adjacent user turns are accepted — so (b) pins the
shape rather than demanding a text rewrite, and (a) pins the explicit merge of adjacent user turns
(`tool_result` blocks first, then the trailing text block).

History messages are built with the real helpers the triage loop uses
(`core.llm.tool_calls_message`, `worker.prompts.build_tool_result_message`,
`worker.triage.FINAL_VERDICT_INSTRUCTION` / `RETRY_INSTRUCTION`). Only the SDK seam is faked; the
fake is mirrored from `tests/test_anthropic_llm_client.py` (pinned, not imported or edited).
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from core.config import ModelPrice
from core.errors import LLMCallError
from core.llm import ChatMessage, ToolCallRequest, tool_calls_message
from core.schemas.verdict import Verdict
from worker.anthropic_llm_client import AnthropicLLMClient
from worker.prompts import build_tool_result_message
from worker.triage import FINAL_VERDICT_INSTRUCTION, RETRY_INSTRUCTION

_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials",
    "recommended_action": "monitor",
    "escalate": False,
}
_PRICES = {
    "fake-model": ModelPrice(input_per_mtok=Decimal("0.15"), output_per_mtok=Decimal("0.60"))
}
_SECRET = "sk-ant-SUPERSECRET-123"
_USAGE = SimpleNamespace(input_tokens=100, output_tokens=50)


class _FakeMessages:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self._stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        block = SimpleNamespace(type="text", text=json.dumps(_VERDICT))
        return SimpleNamespace(content=[block], stop_reason=self._stop_reason, usage=_USAGE)


class _FakeAnthropic:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.messages = _FakeMessages(stop_reason)


def _make(stop_reason: str = "end_turn") -> tuple[AnthropicLLMClient, _FakeAnthropic]:
    fake = _FakeAnthropic(stop_reason)
    client = AnthropicLLMClient(
        client=fake,  # type: ignore[arg-type]
        prices=_PRICES,
        max_tokens=4096,
        effort="low",
    )
    return client, fake


def _blocks(content: object) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    assert isinstance(content, list)
    return [dict(b) for b in content]


async def _structured(client: AnthropicLLMClient, messages: list[ChatMessage]) -> None:
    await client.complete_structured(messages=messages, response_model=Verdict, model="fake-model")


def _tool_history() -> list[ChatMessage]:
    """system + user + two parallel tool calls answered, as the loop leaves `messages`."""
    calls = (
        ToolCallRequest(id="toolu_A", name="get_session_commands", arguments={"session_id": "s1"}),
        ToolCallRequest(id="toolu_B", name="get_alert_history", arguments={"ip": "1.2.3.4"}),
    )
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "<<<ALERT_DATA>>>data<<<END_ALERT_DATA>>>"},
        tool_calls_message(calls),
        build_tool_result_message("toolu_A", {"commands": ["ls"]}),
        build_tool_result_message("toolu_B", {"alerts": 3}),
    ]


def _assert_wellformed_tool_shape(sent: dict[str, Any]) -> None:
    """Tool-less call over tool history: no tools kwargs, valid blocks, strict alternation."""
    assert "tools" not in sent
    assert "tool_choice" not in sent
    messages = sent["messages"]
    assert messages[0]["role"] == "user"
    roles = [m["role"] for m in messages]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False)), roles
    uses = [b["id"] for m in messages for b in _blocks(m["content"]) if b["type"] == "tool_use"]
    results = [
        b["tool_use_id"]
        for m in messages
        for b in _blocks(m["content"])
        if b["type"] == "tool_result"
    ]
    assert uses == ["toolu_A", "toolu_B"]
    assert results == ["toolu_A", "toolu_B"]


# --- (a) adjacent user turns merge ----------------------------------------------------------


async def test_tool_results_then_forced_final_instruction_merge_into_one_user_message() -> None:
    client, fake = _make()
    await _structured(
        client, [*_tool_history(), {"role": "user", "content": FINAL_VERDICT_INSTRUCTION}]
    )
    messages = fake.messages.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    merged = _blocks(messages[-1]["content"])
    assert [b["type"] for b in merged] == ["tool_result", "tool_result", "text"]
    assert [b["tool_use_id"] for b in merged[:2]] == ["toolu_A", "toolu_B"]
    assert merged[2]["text"] == FINAL_VERDICT_INSTRUCTION


async def test_two_consecutive_plain_user_messages_merge_in_order() -> None:
    client, fake = _make()
    await _structured(
        client,
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ],
    )
    messages = fake.messages.calls[0]["messages"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert [(b["type"], b["text"]) for b in _blocks(messages[0]["content"])] == [
        ("text", "first"),
        ("text", "second"),
    ]


# --- (b) tool-less structured call over tool history (pins) ---------------------------------


async def test_forced_final_verdict_after_loop_cap_has_valid_toolless_shape() -> None:
    client, fake = _make()
    history: list[ChatMessage] = [
        *_tool_history(),
        {"role": "user", "content": FINAL_VERDICT_INSTRUCTION},
    ]
    await _structured(client, history)
    _assert_wellformed_tool_shape(fake.messages.calls[0])


async def test_strong_model_escalation_after_tool_use_has_valid_toolless_shape() -> None:
    client, fake = _make()
    await _structured(client, _tool_history())  # history ends on the tool results
    _assert_wellformed_tool_shape(fake.messages.calls[0])


async def test_validation_retry_after_tool_use_has_valid_toolless_shape() -> None:
    client, fake = _make()
    retry: list[ChatMessage] = [
        *_tool_history(),
        {"role": "assistant", "content": '{"severity": 99}'},
        {"role": "user", "content": RETRY_INSTRUCTION.format(error="severity: too large")},
    ]
    await _structured(client, retry)
    sent = fake.messages.calls[0]
    _assert_wellformed_tool_shape(sent)
    assert [m["role"] for m in sent["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]


async def test_validation_retry_after_forced_final_has_valid_toolless_shape() -> None:
    client, fake = _make()
    retry: list[ChatMessage] = [
        *_tool_history(),
        {"role": "user", "content": FINAL_VERDICT_INSTRUCTION},
        {"role": "assistant", "content": "not json"},
        {"role": "user", "content": RETRY_INSTRUCTION.format(error="bad json")},
    ]
    await _structured(client, retry)
    _assert_wellformed_tool_shape(fake.messages.calls[0])


# --- (c) malformed history tool-call arguments ----------------------------------------------


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", '"a string"', "42"])
async def test_malformed_history_tool_arguments_raise_llm_call_error(arguments: str) -> None:
    client, fake = _make()
    history: list[ChatMessage] = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_A",
                    "type": "function",
                    "function": {"name": f"t{_SECRET}", "arguments": arguments},
                }
            ],
        },
        build_tool_result_message("toolu_A", {"ok": True}),
    ]
    with pytest.raises(LLMCallError) as info:
        await _structured(client, history)
    assert _SECRET not in str(info.value)
    assert fake.messages.calls == []


# --- (d) tool message without tool_call_id --------------------------------------------------


async def test_tool_message_missing_tool_call_id_raises_llm_call_error() -> None:
    client, fake = _make()
    history: list[ChatMessage] = [
        {"role": "user", "content": "hi"},
        tool_calls_message((ToolCallRequest(id="toolu_A", name="t", arguments={}),)),
        {"role": "tool", "content": "result without an id"},
    ]
    with pytest.raises(LLMCallError):
        await _structured(client, history)
    assert fake.messages.calls == []


# --- (e) context-window stop reason fails closed --------------------------------------------


async def test_model_context_window_exceeded_stop_reason_fails_closed() -> None:
    client, _ = _make("model_context_window_exceeded")
    with pytest.raises(LLMCallError):
        await _structured(client, [{"role": "user", "content": "hi"}])
