"""Pins `api/routes/stream.py`'s pure SSE-framing helpers, `StreamGate`, and
`verdict_event_stream` (PRD §8 `/stream` row, §10.6) — m8a task-01.

Two properties carry the security/reliability weight of this task (task-01 brief, Goal):

1. `render_verdict_event` is the ONLY place a `verdict.created` channel message becomes wire
   bytes. Because it serializes a validated `VerdictCreatedEvent` with `model_dump_json()`
   (never string interpolation), a `summary` containing a newline can never start a second,
   forged SSE event — `test_render_verdict_event_cannot_be_escaped_by_a_newline_in_summary` pins
   this directly.
2. `StreamGate` bounds concurrent clients; the PAIRING CONTRACT is: the route acquires, the
   `verdict_event_stream` generator releases in its `finally` — on normal end AND on
   `aclose()` (Starlette always closes a `StreamingResponse`'s generator).

`_FakeRedis`/`_FakePubSub` are the one external seam these tests replace (`.claude/rules/tests.md`
"mock only external seams") — a scripted `redis.asyncio.Redis`/`PubSub` double, never our own
code. `worker.publish.verdict_created_payload` builds every "real" payload used below so these
tests cannot drift from the actual publisher (task-01 brief, Interfaces → test table).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
import redis.exceptions

from api.routes.stream import (
    HEARTBEAT_COMMENT,
    VERDICT_CREATED_EVENT,
    StreamGate,
    format_comment,
    format_sse,
    render_verdict_event,
    verdict_event_stream,
)
from core.queue import VERDICT_CREATED_CHANNEL
from core.schemas.verdict import Verdict
from worker.publish import verdict_created_payload

_BASE_PAYLOAD: dict[str, Any] = {
    "alert_id": str(uuid.uuid4()),
    "verdict_id": str(uuid.uuid4()),
    "severity": 3,
    "category": "scanning",
    "escalate": False,
    "summary": "repeated port scans from a known scanner range",
}


def _verdict() -> Verdict:
    """A plain, valid verdict — enough to build a real `verdict_created_payload(...)`."""
    return Verdict(
        severity=3,
        category="scanning",
        confidence=0.7,
        reasoning="repeated port scans from a known scanner range, no follow-up observed",
        recommended_action="continue monitoring",
        escalate=False,
    )


def _extract_data(frame: str) -> dict[str, Any]:
    """Pull the JSON body out of one `event: ...\\ndata: ...\\n\\n` frame."""
    assert frame.endswith("\n\n")
    body = frame[: -len("\n\n")]
    data_lines = [line[len("data: ") :] for line in body.split("\n") if line.startswith("data: ")]
    assert len(data_lines) == 1
    return json.loads(data_lines[0])


class _FakePubSub:
    """A minimal fake of `redis.asyncio.client.PubSub`, scripted with a fixed list of
    `get_message` outcomes (a message dict, `None` for idle, or an exception instance to raise).
    The only external seam `verdict_event_stream` touches (`.claude/rules/tests.md`)."""

    def __init__(self, events: Sequence[dict[str, Any] | BaseException | None]) -> None:
        self._events = list(events)
        self.subscribed_to: str | None = None
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed_to = channel

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float
    ) -> dict[str, Any] | None:
        if not self._events:
            # A test over-consuming its own script is a test bug, not something to hang on.
            raise AssertionError("no more scripted pubsub events")
        event = self._events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    async def unsubscribe(self) -> None:
        self.unsubscribed = True

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    """A fake `redis.asyncio.Redis` exposing only the one method `verdict_event_stream` calls."""

    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


def test_format_sse_and_comment_frames() -> None:
    assert (
        format_sse("verdict.created", '{"severity": 1}')
        == 'event: verdict.created\ndata: {"severity": 1}\n\n'
    )
    assert format_comment("heartbeat") == ": heartbeat\n\n"


def test_render_verdict_event_round_trips_a_published_payload() -> None:
    alert_id = uuid.uuid4()
    verdict_id = uuid.uuid4()
    verdict = _verdict()
    payload = verdict_created_payload(alert_id=alert_id, verdict_id=verdict_id, verdict=verdict)
    raw = json.dumps(payload).encode()

    frame = render_verdict_event(raw)

    assert frame is not None
    assert frame.startswith(f"event: {VERDICT_CREATED_EVENT}\ndata: ")
    assert frame.endswith("\n\n")
    assert _extract_data(frame) == {
        "alert_id": str(alert_id),
        "verdict_id": str(verdict_id),
        "severity": payload["severity"],
        "category": payload["category"],
        "escalate": payload["escalate"],
        "summary": payload["summary"],
    }


@pytest.mark.parametrize(
    "forged_summary",
    [
        'a\n\nevent: verdict.created\ndata: {"severity": 1}',
        'a\r\revent: verdict.created\rdata: {"severity": 1}',
    ],
    ids=["newline", "carriage-return"],
)
def test_render_verdict_event_cannot_be_escaped_by_a_newline_in_summary(
    forged_summary: str,
) -> None:
    """The load-bearing test (task-01 brief, Goal #1): `summary` is attacker-influenced model
    text (PRD §10.6). `model_dump_json()` escapes `\\n`/`\\r` inside it to the two-character
    sequences `\\n`/`\\r` — never a raw newline byte — so this forged "...\\ndata: ..." text can
    never start a second physical SSE line; it only ever sits, inert, inside the one `data:`
    line's JSON string value.

    Judgment call (recorded in the test-author report): pins the same physical-line property the
    brief's `frame.count("event:") == 1` was reaching for, but counts LINES (split on the real
    `\\n` byte), not the raw substring — `frame.count("event:")` is actually 2 for this exact
    payload (the literal words "event: verdict.created" survive JSON-escaping unchanged inside
    the `summary` string value; only the control characters around them are escaped), so the
    brief's literal substring-count assertion cannot pass for ANY implementation, secure or not.
    """
    payload = {**_BASE_PAYLOAD, "summary": forged_summary}
    raw = json.dumps(payload).encode()

    frame = render_verdict_event(raw)

    assert frame is not None
    lines = frame.split("\n")
    assert frame.count("\ndata:") == 1
    assert sum(1 for line in lines if line.startswith("event:")) == 1
    assert sum(1 for line in lines if line.startswith("data:")) == 1
    assert frame.count("\n") == 3  # event line + data line + the blank terminator line
    assert frame.endswith("\n\n")
    assert not frame.endswith("\n\n\n")


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        json.dumps({**_BASE_PAYLOAD, "severity": 9}).encode(),
        json.dumps({**_BASE_PAYLOAD, "unexpected": "nope"}).encode(),
        b"\xff",
    ],
    ids=["bad-json", "bad-schema-severity", "bad-schema-extra-key", "bad-bytes"],
)
def test_render_verdict_event_returns_none_for_bad_json_bad_schema_and_bad_bytes(
    raw: bytes,
) -> None:
    assert render_verdict_event(raw) is None


def test_stream_gate_acquires_up_to_the_limit_and_releases() -> None:
    gate = StreamGate(limit=2)

    assert gate.acquire() is True
    assert gate.acquire() is True
    assert gate.acquire() is False

    gate.release()
    assert gate.acquire() is True

    gate.release()
    gate.release()
    gate.release()  # one extra release than acquires — must clamp at 0, never go negative
    assert gate.active == 0


async def test_verdict_event_stream_yields_heartbeats_on_idle_and_on_socket_timeout() -> None:
    alert_id = uuid.uuid4()
    verdict_id = uuid.uuid4()
    verdict = _verdict()
    payload = verdict_created_payload(alert_id=alert_id, verdict_id=verdict_id, verdict=verdict)
    message = {"type": "message", "data": json.dumps(payload).encode()}

    pubsub = _FakePubSub([None, redis.exceptions.TimeoutError("slow"), message])
    fake_redis = _FakeRedis(pubsub)

    stream = verdict_event_stream(fake_redis, heartbeat_s=5.0)
    first = await anext(stream)
    second = await anext(stream)
    third = await anext(stream)
    await stream.aclose()

    assert first == format_comment(HEARTBEAT_COMMENT)
    assert second == format_comment(HEARTBEAT_COMMENT)
    assert third == render_verdict_event(message["data"])
    assert pubsub.subscribed_to == VERDICT_CREATED_CHANNEL


async def test_verdict_event_stream_ends_on_connection_error_and_logs_the_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pubsub = _FakePubSub(
        [redis.exceptions.ConnectionError("primary-redis.internal:6379 unreachable")]
    )
    fake_redis = _FakeRedis(pubsub)

    with caplog.at_level(logging.WARNING):
        frames = [frame async for frame in verdict_event_stream(fake_redis, heartbeat_s=5.0)]

    assert frames == []
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any("ConnectionError" in message for message in warnings)
    assert not any("primary-redis.internal:6379 unreachable" in message for message in warnings)
    assert not any("unreachable" in message for message in warnings)
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


async def test_verdict_event_stream_releases_the_gate_on_normal_end_and_on_close() -> None:
    gate = StreamGate(limit=1)

    # Normal end: the fake pubsub raises immediately, so the generator returns on its own.
    assert gate.acquire() is True
    ended_pubsub = _FakePubSub([redis.exceptions.ConnectionError("down")])
    frames = [
        frame
        async for frame in verdict_event_stream(
            _FakeRedis(ended_pubsub), heartbeat_s=5.0, gate=gate
        )
    ]
    assert frames == []
    assert gate.active == 0

    # Mid-stream close: Starlette calls `aclose()` on client disconnect / shutdown.
    assert gate.acquire() is True
    idle_pubsub = _FakePubSub([None, None, None])
    stream = verdict_event_stream(_FakeRedis(idle_pubsub), heartbeat_s=5.0, gate=gate)
    frame = await anext(stream)
    assert frame == format_comment(HEARTBEAT_COMMENT)

    await stream.aclose()

    assert gate.active == 0
