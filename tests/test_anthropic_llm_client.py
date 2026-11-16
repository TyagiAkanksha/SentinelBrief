"""Pins `worker.anthropic_llm_client.AnthropicLLMClient` (anthropic-haiku task 2).

Mirrors `tests/test_llm_client.py` + `tests/test_llm_tools_seam.py` (the `OpenAICompatibleLLMClient`
pins) one-for-one wherever the behavior is provider-neutral, and adds the Anthropic-specific wire
facts from `.superpowers/sdd/anthropic-haiku/brief.md`: top-level `system=`, `output_config`
carrying `effort`, no `temperature`/`top_p`/`top_k`, no assistant prefill, tool results as one
merged `user` message of `tool_result` blocks, `max_tokens`/`refusal` stop reasons fail closed.

External seam only: a hand-rolled fake of `anthropic.AsyncAnthropic` (`messages.create` async) —
no network. Deliberately NOT pinned: whether structured output rides on
`output_config.format = json_schema` or schema-in-prompt (implementer's call per the brief) — only
that the reply is validated and `effort` is in `output_config`.

Dependency notes: needs the `anthropic` package importable (not yet in pyproject at authoring
time) and the Task 1 `Settings` fields `llm_provider` / `llm_effort` / `llm_max_tokens`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from core.config import ModelPrice, Settings
from core.errors import ConfigError, LLMCallError, StructuredOutputError
from core.llm import (
    ChatMessage,
    LLMResult,
    LLMUsage,
    ToolCallRequest,
    ToolCallTurn,
    ToolSpec,
    compute_cost_usd,
    tool_calls_message,
)
from core.schemas.verdict import Verdict
from worker.anthropic_llm_client import AnthropicLLMClient

_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials",
    "recommended_action": "monitor",
    "escalate": False,
}

_FAKE_PRICES = {
    "fake-model": ModelPrice(input_per_mtok=Decimal("0.15"), output_per_mtok=Decimal("0.60"))
}

_SESSION_TOOL_SPEC: ToolSpec = {
    "name": "get_session_commands",
    "description": "Return the full command list for a session.",
    "parameters": {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
    },
}

_SECRET = "sk-ant-SUPERSECRET-123"


# --- fake SDK seam ---------------------------------------------------------------------------


_DEFAULT_USAGE = SimpleNamespace(input_tokens=100, output_tokens=50)


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _thinking(text: str = "let me think") -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking=text, signature="sig")


def _tool_use(call_id: str, name: str, tool_input: object) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=tool_input)


def _response(
    blocks: list[SimpleNamespace],
    *,
    stop_reason: str = "end_turn",
    usage: SimpleNamespace | None = _DEFAULT_USAGE,
) -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage)


def _verdict_response() -> SimpleNamespace:
    return _response([_thinking(), _text(json.dumps(_VALID_VERDICT))])


class _FakeMessages:
    def __init__(self, reply: SimpleNamespace | Exception) -> None:
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


class _FakeAnthropic:
    def __init__(self, reply: SimpleNamespace | Exception) -> None:
        self.messages = _FakeMessages(reply)


def _make(
    reply: SimpleNamespace | Exception,
    *,
    max_tokens: int = 4096,
    effort: str = "low",
) -> tuple[AnthropicLLMClient, _FakeAnthropic]:
    fake = _FakeAnthropic(reply)
    client = AnthropicLLMClient(
        client=fake,  # type: ignore[arg-type]
        prices=_FAKE_PRICES,
        max_tokens=max_tokens,
        effort=effort,  # type: ignore[arg-type]
    )
    return client, fake


def _kwargs(fake: _FakeAnthropic) -> dict[str, Any]:
    assert len(fake.messages.calls) == 1
    return fake.messages.calls[0]


def _blocks(content: object) -> list[dict[str, Any]]:
    """Normalise a message `content` (str or block list) to a list of block dicts."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    assert isinstance(content, list)
    return [dict(b) for b in content]


def _system_text(kwargs: dict[str, Any]) -> str:
    system = kwargs["system"]
    if isinstance(system, str):
        return system
    return "\n".join(b["text"] for b in system)


async def _structured(client: AnthropicLLMClient) -> LLMResult[Verdict]:
    return await client.complete_structured(
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "<<<ALERT_DATA>>>data<<<END_ALERT_DATA>>>"},
        ],
        response_model=Verdict,
        model="fake-model",
    )


async def _with_tools(
    client: AnthropicLLMClient, messages: Sequence[ChatMessage] | None = None
) -> LLMResult[Verdict] | ToolCallTurn:
    return await client.complete_with_tools(
        messages=messages or [{"role": "user", "content": "hi"}],
        tools=[_SESSION_TOOL_SPEC],
        response_model=Verdict,
        model="fake-model",
    )


# --- request kwargs --------------------------------------------------------------------------


async def test_structured_request_kwargs() -> None:
    client, fake = _make(_verdict_response(), max_tokens=1234, effort="high")

    result = await _structured(client)

    assert isinstance(result.parsed, Verdict)
    kwargs = _kwargs(fake)
    assert kwargs["model"] == "fake-model"
    assert kwargs["max_tokens"] == 1234
    assert kwargs["output_config"]["effort"] == "high"
    assert "system prompt" in _system_text(kwargs)
    assert all(m["role"] != "system" for m in kwargs["messages"])
    assert [m["role"] for m in kwargs["messages"]] == ["user"]
    assert "<<<ALERT_DATA>>>data<<<END_ALERT_DATA>>>" in json.dumps(kwargs["messages"])
    # complete_structured offers no tools
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


async def test_never_sends_sampling_params() -> None:
    """Haiku 5.5 400s on non-default temperature/top_p/top_k (brief)."""
    client, fake = _make(_verdict_response())

    await _structured(client)
    for key in ("temperature", "top_p", "top_k"):
        assert key not in _kwargs(fake)

    client2, fake2 = _make(_verdict_response())
    await _with_tools(client2)
    for key in ("temperature", "top_p", "top_k"):
        assert key not in _kwargs(fake2)


async def test_never_prefills_assistant_turn() -> None:
    """The last message sent must be a user turn (assistant prefill 400s on Haiku 5.5)."""
    client, fake = _make(_verdict_response())

    await _structured(client)

    assert _kwargs(fake)["messages"][-1]["role"] == "user"


async def test_multiple_system_messages_all_reach_system_param() -> None:
    client, fake = _make(_verdict_response())

    await client.complete_structured(
        messages=[
            {"role": "system", "content": "SYS-ONE"},
            {"role": "system", "content": "SYS-TWO"},
            {"role": "user", "content": "hi"},
        ],
        response_model=Verdict,
        model="fake-model",
    )

    kwargs = _kwargs(fake)
    assert "SYS-ONE" in _system_text(kwargs)
    assert "SYS-TWO" in _system_text(kwargs)
    assert [m["role"] for m in kwargs["messages"]] == ["user"]


async def test_effort_comes_from_constructor() -> None:
    for effort in ("low", "max"):
        client, fake = _make(_verdict_response(), effort=effort)
        await _structured(client)
        assert _kwargs(fake)["output_config"]["effort"] == effort


# --- tools wire shape ------------------------------------------------------------------------


async def test_tools_wire_shape_and_tool_choice_auto() -> None:
    client, fake = _make(_verdict_response())

    await _with_tools(client)

    kwargs = _kwargs(fake)
    assert kwargs["tools"] == [
        {
            "name": "get_session_commands",
            "description": "Return the full command list for a session.",
            "input_schema": _SESSION_TOOL_SPEC["parameters"],
        }
    ]
    assert kwargs["tool_choice"] == {"type": "auto"}
    assert kwargs["output_config"]["effort"] == "low"


# --- OpenAI-shaped history conversion --------------------------------------------------------


async def test_history_converts_tool_calls_and_merges_results() -> None:
    client, fake = _make(_verdict_response())
    calls = [
        ToolCallRequest(id="toolu_1", name="get_session_commands", arguments={"session_id": "a"}),
        ToolCallRequest(id="toolu_2", name="get_session_commands", arguments={"session_id": "b"}),
    ]
    messages: list[ChatMessage] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "triage this"},
        tool_calls_message(calls),
        {"role": "tool", "tool_call_id": "toolu_1", "content": "result one"},
        {"role": "tool", "tool_call_id": "toolu_2", "content": "result two"},
    ]

    await _with_tools(client, messages)

    sent = _kwargs(fake)["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]

    assistant_blocks = _blocks(sent[1]["content"])
    tool_use = [b for b in assistant_blocks if b["type"] == "tool_use"]
    assert [(b["id"], b["name"], b["input"]) for b in tool_use] == [
        ("toolu_1", "get_session_commands", {"session_id": "a"}),
        ("toolu_2", "get_session_commands", {"session_id": "b"}),
    ]
    # input is a dict, not JSON text
    assert all(isinstance(b["input"], dict) for b in tool_use)
    # no empty text block (the API rejects those) from the empty-content tool-call turn
    assert all(b.get("text") for b in assistant_blocks if b["type"] == "text")

    result_blocks = _blocks(sent[2]["content"])
    assert [b["type"] for b in result_blocks] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in result_blocks] == ["toolu_1", "toolu_2"]
    assert [b["content"] for b in result_blocks] == ["result one", "result two"]


async def test_history_plain_assistant_text_passes_through() -> None:
    client, fake = _make(_verdict_response())

    await client.complete_structured(
        messages=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "follow up"},
        ],
        response_model=Verdict,
        model="fake-model",
    )

    sent = _kwargs(fake)["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert _blocks(sent[1]["content"]) == [{"type": "text", "text": "earlier answer"}]


# --- success path: usage / cost / latency ----------------------------------------------------


async def test_structured_result_usage_cost_latency_and_thinking_ignored() -> None:
    reply = _response(
        [_thinking(), _text('{"severity": 2, '), _text(json.dumps(_VALID_VERDICT)[1:])]
    )
    client, _ = _make(reply)

    result = await _structured(client)

    assert isinstance(result, LLMResult)
    assert result.parsed == Verdict.model_validate(_VALID_VERDICT)
    assert result.model == "fake-model"
    assert result.usage == LLMUsage(100, 50)
    assert result.cost_usd == compute_cost_usd(_FAKE_PRICES["fake-model"], LLMUsage(100, 50))
    assert result.cost_usd == Decimal("0.000045")
    assert isinstance(result.latency_ms, int)
    assert result.latency_ms >= 0
    assert "let me think" not in result.raw_text


async def test_text_blocks_are_concatenated() -> None:
    body = json.dumps(_VALID_VERDICT)
    client, _ = _make(_response([_text(body[:10]), _thinking(), _text(body[10:])]))

    result = await _structured(client)

    assert result.raw_text == body


async def test_complete_with_tools_content_reply_is_llm_result() -> None:
    client, _ = _make(_verdict_response())

    result = await _with_tools(client)

    assert isinstance(result, LLMResult)
    assert result.parsed == Verdict.model_validate(_VALID_VERDICT)
    assert result.usage == LLMUsage(100, 50)
    assert result.cost_usd == Decimal("0.000045")


# --- structured-output failures --------------------------------------------------------------


async def test_invalid_json_raises_structured_output_error() -> None:
    client, _ = _make(_response([_text("not json at all")]))

    with pytest.raises(StructuredOutputError) as info:
        await _structured(client)

    assert info.value.raw_text == "not json at all"


async def test_schema_invalid_reply_raises_structured_output_error() -> None:
    bad = dict(_VALID_VERDICT, severity=9)
    client, _ = _make(_response([_text(json.dumps(bad))]))

    with pytest.raises(StructuredOutputError):
        await _structured(client)


async def test_tools_content_reply_invalid_raises_structured_output_error() -> None:
    client, _ = _make(_response([_text("{}")]))

    with pytest.raises(StructuredOutputError):
        await _with_tools(client)


async def test_no_text_content_raises_llm_call_error() -> None:
    client, _ = _make(_response([_thinking()]))

    with pytest.raises(LLMCallError):
        await _structured(client)


async def test_empty_content_list_raises_llm_call_error() -> None:
    client, _ = _make(_response([]))

    with pytest.raises(LLMCallError):
        await _structured(client)


# --- tool-call replies -----------------------------------------------------------------------


async def test_single_tool_use_becomes_tool_call_turn() -> None:
    reply = _response(
        [_thinking(), _tool_use("toolu_1", "get_session_commands", {"session_id": "abc"})],
        stop_reason="tool_use",
    )
    client, _ = _make(reply)

    turn = await _with_tools(client)

    assert isinstance(turn, ToolCallTurn)
    assert turn.calls == (
        ToolCallRequest(id="toolu_1", name="get_session_commands", arguments={"session_id": "abc"}),
    )
    assert turn.model == "fake-model"
    assert turn.usage == LLMUsage(100, 50)
    assert turn.cost_usd == Decimal("0.000045")
    assert isinstance(turn.latency_ms, int)


async def test_parallel_tool_use_blocks_keep_order() -> None:
    reply = _response(
        [
            _text("calling two tools"),
            _tool_use("toolu_1", "get_session_commands", {"session_id": "a"}),
            _tool_use("toolu_2", "get_session_commands", {"session_id": "b"}),
        ],
        stop_reason="tool_use",
    )
    client, _ = _make(reply)

    turn = await _with_tools(client)

    assert isinstance(turn, ToolCallTurn)
    assert [c.id for c in turn.calls] == ["toolu_1", "toolu_2"]
    assert [c.arguments for c in turn.calls] == [{"session_id": "a"}, {"session_id": "b"}]


async def test_empty_tool_input_is_empty_dict() -> None:
    reply = _response([_tool_use("toolu_1", "get_session_commands", {})], stop_reason="tool_use")
    client, _ = _make(reply)

    turn = await _with_tools(client)

    assert isinstance(turn, ToolCallTurn)
    assert turn.calls[0].arguments == {}


async def test_non_dict_tool_input_raises_llm_call_error() -> None:
    for bad in (["a"], "text", 5, None):
        reply = _response(
            [_tool_use("toolu_1", "get_session_commands", bad)], stop_reason="tool_use"
        )
        client, _ = _make(reply)

        with pytest.raises(LLMCallError):
            await _with_tools(client)


async def test_empty_tools_raises_value_error_before_provider_call() -> None:
    client, fake = _make(_verdict_response())

    with pytest.raises(ValueError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            response_model=Verdict,
            model="fake-model",
        )

    assert fake.messages.calls == []


# --- pricing / error mapping -----------------------------------------------------------------


async def test_unpriced_model_raises_config_error_before_any_provider_call() -> None:
    client, fake = _make(_verdict_response())

    with pytest.raises(ConfigError):
        await client.complete_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_model=Verdict,
            model="not-priced",
        )
    with pytest.raises(ConfigError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="not-priced",
        )

    assert fake.messages.calls == []


_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIError("boom", _REQUEST, body=None),
        anthropic.APIConnectionError(request=_REQUEST),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_sdk_and_http_errors_map_to_llm_call_error(error: Exception) -> None:
    for call in ("structured", "tools"):
        client, _ = _make(error)
        with pytest.raises(LLMCallError) as info:
            await (_structured(client) if call == "structured" else _with_tools(client))
        assert _SECRET not in str(info.value)


async def test_max_tokens_stop_reason_fails_closed() -> None:
    """Thinking tokens count against max_tokens; a truncated reply must not be parsed."""
    truncated = _response([_text(json.dumps(_VALID_VERDICT))], stop_reason="max_tokens")
    for call in ("structured", "tools"):
        client, _ = _make(truncated)
        with pytest.raises(LLMCallError):
            await (_structured(client) if call == "structured" else _with_tools(client))


async def test_refusal_stop_reason_fails_closed() -> None:
    refused = _response([_text(json.dumps(_VALID_VERDICT))], stop_reason="refusal")
    for call in ("structured", "tools"):
        client, _ = _make(refused)
        with pytest.raises(LLMCallError):
            await (_structured(client) if call == "structured" else _with_tools(client))


async def test_missing_usage_raises_llm_call_error() -> None:
    reply = _response([_text(json.dumps(_VALID_VERDICT))], usage=None)
    for call in ("structured", "tools"):
        client, _ = _make(reply)
        with pytest.raises(LLMCallError):
            await (_structured(client) if call == "structured" else _with_tools(client))


# --- from_settings ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_provider": "anthropic",
        "cheap_model": "m",
        "model_prices_json": {"m": {"input_per_mtok": "0.10", "output_per_mtok": "0.50"}},
    }
    base.update(overrides)
    return Settings(**base)


def test_from_settings_builds_anthropic_sdk_client() -> None:
    settings = _settings(llm_api_key=_SECRET, llm_timeout_s=12.5)

    client = AnthropicLLMClient.from_settings(settings)

    sdk_client = client._client  # type: ignore[attr-defined]
    assert isinstance(sdk_client, anthropic.AsyncAnthropic)
    assert sdk_client.api_key == _SECRET
    assert sdk_client.timeout == 12.5
    assert sdk_client.max_retries == 2


def test_from_settings_empty_key_falls_back_to_unset_placeholder() -> None:
    client = AnthropicLLMClient.from_settings(_settings())

    assert client._client.api_key == "unset"  # type: ignore[attr-defined]


def test_from_settings_raises_when_cheap_model_empty() -> None:
    with pytest.raises(ConfigError):
        AnthropicLLMClient.from_settings(_settings(cheap_model="", model_prices_json={}))


def test_from_settings_raises_when_model_unpriced() -> None:
    with pytest.raises(ConfigError):
        AnthropicLLMClient.from_settings(_settings(model_prices_json={}))


def test_from_settings_raises_when_strong_model_unpriced() -> None:
    with pytest.raises(ConfigError):
        AnthropicLLMClient.from_settings(_settings(strong_model="strong-unpriced"))


async def test_from_settings_threads_max_tokens_and_effort_into_requests() -> None:
    settings = _settings(llm_max_tokens=777, llm_effort="xhigh")
    client = AnthropicLLMClient.from_settings(settings)
    fake = _FakeAnthropic(_verdict_response())
    client._client = fake  # type: ignore[attr-defined]  # swap the external SDK seam only

    await client.complete_structured(
        messages=[{"role": "user", "content": "hi"}], response_model=Verdict, model="m"
    )

    kwargs = _kwargs(fake)
    assert kwargs["max_tokens"] == 777
    assert kwargs["output_config"]["effort"] == "xhigh"
