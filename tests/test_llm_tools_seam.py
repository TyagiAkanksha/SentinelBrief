"""Pins `core.llm`'s tool-calling seam (`ToolCallRequest`, `ToolCallTurn`, `ToolSpec`,
`tool_calls_message`, `LLMClient.complete_with_tools`) on `OpenAICompatibleLLMClient` and
`tests.fakes.FakeLLMClient` (m4 task-01).

PRD §6.3 (the model decides which tools to call, results are fed back), §6.5 (the final reply is
still the `Verdict` JSON object), §10.1 (LLM calls only happen in `worker/`). CONVENTIONS.md §2
contract 3 (`core.llm` carries no SDK import) and §10 (`FakeLLMClient` validates content replies
through the real `parse_structured` path, exactly like `complete_structured`, so a bad JSON reply
fails identically in tests and production).
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import httpx
import openai
import pytest

from core.config import ModelPrice
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
from tests.fakes import FakeLLMClient, ScriptedToolCall
from worker.llm_client import OpenAICompatibleLLMClient

# A valid Verdict reply body, shared across the tests that need one (mirrors test_llm_client.py).
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


def _chat_completion_body(content: str) -> dict[str, object]:
    """Build a minimal OpenAI-shaped chat-completion response body around `content`."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _tool_call_member(
    call_id: str, name: str, arguments: str, *, call_type: str = "function"
) -> dict[str, object]:
    """One `choices[0].message.tool_calls[i]` member, openai SDK 3.8.0 shape (task-01 brief)."""
    return {"id": call_id, "type": call_type, "function": {"name": name, "arguments": arguments}}


def _chat_completion_tool_calls_body(tool_calls: list[dict[str, object]]) -> dict[str, object]:
    """A chat-completion response body whose message asks for `tool_calls`."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _mock_client(
    handler: httpx.MockTransport | None, *, max_retries: int = 2
) -> openai.AsyncOpenAI:
    """Build an `AsyncOpenAI` SDK client whose HTTP transport is fully mocked."""
    assert handler is not None
    return openai.AsyncOpenAI(
        api_key="test",
        base_url="http://test/v1",
        http_client=httpx.AsyncClient(transport=handler),
        max_retries=max_retries,
    )


async def _complete_with_tools_bad_arguments(bad_arguments: str) -> None:
    """Drive `complete_with_tools` through one non-object `arguments` string; helper shared by
    the two cases in `test_complete_with_tools_rejects_non_object_arguments`.
    """
    tool_calls = [_tool_call_member("call_1", "get_session_commands", bad_arguments)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion_tool_calls_body(tool_calls))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(LLMCallError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )


def test_tool_calls_message_builds_the_assistant_wire_shape() -> None:
    calls = (
        ToolCallRequest(id="call_1", name="get_session_commands", arguments={"session_id": "s1"}),
        ToolCallRequest(id="call_2", name="get_ip_geo_asn", arguments={"ip": "203.0.113.10"}),
    )

    message = tool_calls_message(calls)

    assert message["role"] == "assistant"
    assert message["content"] == ""
    assert message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_session_commands",
                "arguments": json.dumps({"session_id": "s1"}, sort_keys=True),
            },
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {
                "name": "get_ip_geo_asn",
                "arguments": json.dumps({"ip": "203.0.113.10"}, sort_keys=True),
            },
        },
    ]


def test_tool_call_types_are_frozen() -> None:
    call = ToolCallRequest(id="call_1", name="get_session_commands", arguments={})
    turn = ToolCallTurn(
        calls=(call,),
        model="fake-model",
        usage=LLMUsage(100, 50),
        cost_usd=Decimal("0.0001"),
        latency_ms=5,
    )

    with pytest.raises(FrozenInstanceError):
        turn.calls = ()  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        call.name = "other"  # type: ignore[misc]


async def test_complete_with_tools_sends_tools_tool_choice_json_mode_and_temperature_zero() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion_body(json.dumps(_VALID_VERDICT)))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )
    messages: list[ChatMessage] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "<<<ALERT_DATA>>>data<<<END_ALERT_DATA>>>"},
    ]

    result = await client.complete_with_tools(
        messages=messages,
        tools=[_SESSION_TOOL_SPEC],
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(result, LLMResult)
    assert len(captured) == 1
    body = captured[0]
    assert body["tools"] == [{"type": "function", "function": _SESSION_TOOL_SPEC}]
    assert body["tool_choice"] == "auto"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] in (0, 0.0)
    assert body["messages"] == messages


async def test_complete_with_tools_returns_tool_call_turn_with_decoded_arguments_usage_and_cost() -> (  # noqa: E501
    None
):  # noqa: E501
    tool_calls = [
        _tool_call_member("call_1", "get_session_commands", json.dumps({"session_id": "abc"})),
        _tool_call_member("call_2", "get_ip_geo_asn", json.dumps({"ip": "203.0.113.10"})),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion_tool_calls_body(tool_calls))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    turn = await client.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[_SESSION_TOOL_SPEC],
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(turn, ToolCallTurn)
    assert turn.calls[0].id == "call_1"
    assert turn.calls[0].name == "get_session_commands"
    assert turn.calls[0].arguments == {"session_id": "abc"}
    assert turn.calls[1].id == "call_2"
    assert turn.calls[1].name == "get_ip_geo_asn"
    assert turn.calls[1].arguments == {"ip": "203.0.113.10"}
    assert turn.model == "fake-model"
    assert turn.usage == LLMUsage(100, 50)
    expected_cost = compute_cost_usd(_FAKE_PRICES["fake-model"], LLMUsage(100, 50))
    assert turn.cost_usd == expected_cost
    assert turn.cost_usd != Decimal("0")


async def test_complete_with_tools_content_reply_validates_like_complete_structured() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion_body(json.dumps(_VALID_VERDICT)))

    ok_client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(ok_handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    result = await ok_client.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[_SESSION_TOOL_SPEC],
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(result, LLMResult)
    assert result.parsed.category == "brute_force"

    def bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion_body("not json"))

    bad_client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(bad_handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(StructuredOutputError) as exc_info:
        await bad_client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )
    assert exc_info.value.raw_text == "not json"
    assert exc_info.value.input_tokens == 100
    assert exc_info.value.output_tokens == 50


async def test_complete_with_tools_rejects_non_object_arguments() -> None:
    await _complete_with_tools_bad_arguments("[1, 2]")
    await _complete_with_tools_bad_arguments("{")


async def test_complete_with_tools_empty_or_whitespace_arguments_decode_to_empty_dict() -> None:
    """m4 fix-wave (review finding task-01 M5): some OpenAI-compatible servers send
    `function.arguments == ""` (or whitespace-only) for a tool call that takes no parameters,
    instead of the spec-correct `"{}"`. `json.loads("")` raises `json.JSONDecodeError`, which
    today maps to `LLMCallError` and fails the whole alert (`worker/triage.py:310` marks it
    `failed`) for a reply the loop should simply treat as `arguments == {}` and continue. Genuinely
    malformed arguments (`"[1, 2]"`, a non-object; `"{"`, truncated JSON) must still raise
    `LLMCallError` — that is `test_complete_with_tools_rejects_non_object_arguments` above, and it
    is unchanged by this fix.
    """
    for bad_arguments in ("", "   "):
        tool_calls = [_tool_call_member("call_1", "get_session_commands", bad_arguments)]

        def handler(
            request: httpx.Request, tool_calls: list[dict[str, object]] = tool_calls
        ) -> httpx.Response:
            return httpx.Response(200, json=_chat_completion_tool_calls_body(tool_calls))

        client = OpenAICompatibleLLMClient(
            client=_mock_client(httpx.MockTransport(handler)),
            prices=_FAKE_PRICES,
            json_mode="json_object",
        )

        turn = await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )

        assert isinstance(turn, ToolCallTurn)
        assert turn.calls[0].arguments == {}


async def test_complete_with_tools_rejects_non_function_tool_call_type() -> None:
    tool_calls = [_tool_call_member("call_1", "get_session_commands", "{}", call_type="custom")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion_tool_calls_body(tool_calls))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(LLMCallError, match="custom"):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )


async def test_complete_with_tools_requires_at_least_one_tool() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_chat_completion_body(json.dumps(_VALID_VERDICT)))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(ValueError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            response_model=Verdict,
            model="fake-model",
        )

    assert calls == []


async def test_complete_with_tools_unpriced_model_never_calls_provider() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_chat_completion_body(json.dumps(_VALID_VERDICT)))

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler)),
        prices={},
        json_mode="json_object",
    )

    with pytest.raises(ConfigError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )

    assert calls == []


async def test_complete_with_tools_maps_http_500_to_llm_call_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler), max_retries=0),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(LLMCallError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )


async def test_complete_with_tools_missing_usage_raises_llm_call_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _chat_completion_body(json.dumps(_VALID_VERDICT))
        del body["usage"]
        return httpx.Response(200, json=body)

    client = OpenAICompatibleLLMClient(
        client=_mock_client(httpx.MockTransport(handler), max_retries=0),
        prices=_FAKE_PRICES,
        json_mode="json_object",
    )

    with pytest.raises(LLMCallError):
        await client.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )


async def test_fake_replays_scripted_tool_calls_with_minted_ids_and_records_tools() -> None:
    fake = FakeLLMClient(
        [
            [ScriptedToolCall("get_session_commands", {"session_id": "s"})],
            json.dumps(_VALID_VERDICT),
        ]
    )
    tools = [_SESSION_TOOL_SPEC]

    first = await fake.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(first, ToolCallTurn)
    assert len(first.calls) == 1
    assert first.calls[0].id == "call_1_1"
    assert first.calls[0].name == "get_session_commands"
    assert first.calls[0].arguments == {"session_id": "s"}
    assert fake.calls[0].tools == list(tools)

    second = await fake.complete_with_tools(
        messages=[{"role": "user", "content": "hi again"}],
        tools=tools,
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(second, LLMResult)
    assert second.parsed.category == "brute_force"
    assert fake.calls[1].tools == list(tools)


async def test_fake_keeps_an_explicit_scripted_id() -> None:
    fake = FakeLLMClient(
        [[ScriptedToolCall("get_session_commands", {"session_id": "s"}, id="abc")]]
    )

    turn = await fake.complete_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        tools=[_SESSION_TOOL_SPEC],
        response_model=Verdict,
        model="fake-model",
    )

    assert isinstance(turn, ToolCallTurn)
    assert turn.calls[0].id == "abc"


async def test_fake_rejects_tool_script_on_complete_structured() -> None:
    fake = FakeLLMClient([[ScriptedToolCall("get_session_commands", {"session_id": "s"})]])

    with pytest.raises(AssertionError):
        await fake.complete_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_model=Verdict,
            model="fake-model",
        )


async def test_fake_complete_with_tools_exhausted_raises() -> None:
    fake = FakeLLMClient([])

    with pytest.raises(AssertionError, match="no responses left"):
        await fake.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )


async def test_fake_complete_structured_records_tools_none() -> None:
    fake = FakeLLMClient([json.dumps(_VALID_VERDICT)])

    await fake.complete_structured(
        messages=[{"role": "user", "content": "hi"}],
        response_model=Verdict,
        model="fake-model",
    )

    assert fake.calls[0].tools is None


# --- m4 task-06 (controller ruling R11): the fake mirrors the real client's own guards ----------


async def test_fake_complete_with_tools_rejects_empty_tools() -> None:
    """Mirrors `test_complete_with_tools_requires_at_least_one_tool` above, on the fake: no reply
    is consumed and no call is recorded — exactly like the real client never issuing a request."""
    fake = FakeLLMClient([json.dumps(_VALID_VERDICT)])

    with pytest.raises(ValueError, match="at least one tool"):
        await fake.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            response_model=Verdict,
            model="fake-model",
        )

    assert fake.calls == []


async def test_fake_complete_with_tools_rejects_empty_scripted_tool_sequence() -> None:
    """An empty `ScriptedToolCall` sequence would mint a `ToolCallTurn` with zero calls —
    `ToolCallTurn.calls` is documented `len >= 1`, so the fake refuses to build one."""
    fake = FakeLLMClient([[]])

    with pytest.raises(AssertionError, match="empty tool script"):
        await fake.complete_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[_SESSION_TOOL_SPEC],
            response_model=Verdict,
            model="fake-model",
        )
