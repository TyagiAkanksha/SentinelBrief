"""Regression pins from the task-01 fix-round-1 review
(`.superpowers/sdd/m8-polish/task-01-review.md`) — a NEW file; adding test files is always
allowed even once a task's other files are pinned (`.claude/rules/tests.md`).

**A1 (Critical C1).** `api/routes/stream.py::verdict_event_stream` calls `redis.pubsub()` and
`await pubsub.subscribe(...)` *before* its outer `try`. Any exception out of either call — a dead
Redis raises `redis.exceptions.ConnectionError`; a client disconnect racing the subscribe delivers
`asyncio.CancelledError` at that same `await` — exits the generator frame without ever reaching
`finally: if gate is not None: gate.release()`, permanently burning one `StreamGate` slot per
failure. The route has already called `gate.acquire()` by the time this generator's first
`__anext__()` runs (`stream_verdicts` acquires, then builds the `StreamingResponse` around the
generator), so these three tests acquire the gate first too, mirroring the route exactly. **All
three must fail against today's code** and go green only once the fix moves the subscribe inside
the outer `try` (mutation self-check in the test-author's fix-round report: reverting that move
must make each of these fail again).

**A2 (M6).** A raising `pubsub.aclose()` after a normal loop exit must not leak the permit either.
Today this already holds, but only incidentally — the cleanup sits inside the outer `try` even
though the subscribe does not — so this pin makes the guarantee testable without depending on
redis-py's real disconnect behaviour ever raising.

**A3 (M3).** The generator's own "log one WARNING, skip the frame, keep streaming" handling of a
`render_verdict_event(...) is None` result was never pinned directly — only `render_verdict_event`'s
`None` return itself was (`tests/test_stream_events.py`). Mutant 8 in the review
(`yield rendered or ""` instead of `if rendered is None: continue`) survived every existing test.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
import redis.exceptions

from api.routes.stream import StreamGate, render_verdict_event, verdict_event_stream
from core.schemas.verdict import Verdict
from worker.publish import verdict_created_payload


def _valid_message() -> dict[str, Any]:
    """One well-formed `{"type": "message", "data": ...}` pubsub event, built from a real
    `Verdict` through `worker.publish.verdict_created_payload` — never a hand-written dict."""
    verdict = Verdict(
        severity=2,
        category="reconnaissance",
        confidence=0.6,
        reasoning="a valid verdict for the stream-hardening pin",
        recommended_action="continue monitoring",
        escalate=False,
    )
    payload = verdict_created_payload(
        alert_id=uuid.uuid4(), verdict_id=uuid.uuid4(), verdict=verdict
    )
    return {"type": "message", "data": json.dumps(payload).encode()}


class _RedisPubSubReturns:
    """A fake Redis client whose synchronous `pubsub()` call returns a fixed double."""

    def __init__(self, pubsub: object) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> object:
        return self._pubsub


class _RedisPubSubRaises:
    """A fake Redis client whose synchronous `pubsub()` call itself raises, before any `PubSub`
    object exists at all."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def pubsub(self) -> object:
        raise self._exc


class _RaisingSubscribePubSub:
    """A `PubSub` double whose `subscribe()` raises before anything is ever read.

    `unsubscribe()`/`aclose()` re-raise the SAME exception rather than asserting they are never
    called: a real dead connection stays dead for those calls too (redis-py's `unsubscribe()`
    sends a command over the same broken connection), and a real disconnect-driven cancellation
    scope keeps re-delivering `CancelledError` at the next checkpoint either way (confirmed
    empirically while authoring the RED round — see that report's Judgment calls). Whether a
    correct fix even attempts cleanup here is an implementation detail this pin does not care
    about; only that SOME instance of the same exception class propagates and the gate is freed.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def subscribe(self, channel: str) -> None:
        raise self._exc

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float
    ) -> dict[str, Any] | None:
        raise AssertionError("get_message must never run: subscribe already raised")

    async def unsubscribe(self) -> None:
        raise self._exc

    async def aclose(self) -> None:
        raise self._exc


class _SubscribeOkThenAcloseRaisesPubSub:
    """A `PubSub` double that subscribes fine, ends the loop through the generator's own
    `except (RedisError, OSError): ... return` branch (a normal exit), and then raises out of
    `aclose()` during cleanup — mirroring a connection that dies between the last read and
    teardown."""

    def __init__(self) -> None:
        self.unsubscribed = False

    async def subscribe(self, channel: str) -> None:
        return None

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float
    ) -> dict[str, Any] | None:
        raise redis.exceptions.ConnectionError("lost the connection mid-read")

    async def unsubscribe(self) -> None:
        self.unsubscribed = True

    async def aclose(self) -> None:
        raise redis.exceptions.ConnectionError("aclose failed too")


class _ScriptedPubSub:
    """A minimal fake of `redis.asyncio.client.PubSub`, scripted with a fixed list of
    `get_message` outcomes — mirrors `tests/test_stream_events.py::_FakePubSub` but lives here so
    this file has no dependency on that pinned file's private helpers."""

    def __init__(self, events: Sequence[dict[str, Any]]) -> None:
        self._events = list(events)

    async def subscribe(self, channel: str) -> None:
        return None

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float
    ) -> dict[str, Any] | None:
        if not self._events:
            raise AssertionError("no more scripted pubsub events")
        return self._events.pop(0)

    async def unsubscribe(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


async def test_gate_releases_when_subscribe_raises_connection_error() -> None:
    gate = StreamGate(limit=2)
    assert gate.acquire() is True  # mirrors stream_verdicts: the route acquires first
    fake_redis = _RedisPubSubReturns(
        _RaisingSubscribePubSub(redis.exceptions.ConnectionError("down"))
    )
    stream = verdict_event_stream(fake_redis, heartbeat_s=5.0, gate=gate)

    with pytest.raises(redis.exceptions.ConnectionError):
        await anext(stream)

    assert gate.active == 0


async def test_gate_releases_when_subscribe_raises_cancelled_error() -> None:
    gate = StreamGate(limit=2)
    assert gate.acquire() is True
    fake_redis = _RedisPubSubReturns(_RaisingSubscribePubSub(asyncio.CancelledError()))
    stream = verdict_event_stream(fake_redis, heartbeat_s=5.0, gate=gate)

    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    assert gate.active == 0


async def test_gate_releases_when_redis_pubsub_itself_raises() -> None:
    gate = StreamGate(limit=2)
    assert gate.acquire() is True
    fake_redis = _RedisPubSubRaises(redis.exceptions.ConnectionError("down"))
    stream = verdict_event_stream(fake_redis, heartbeat_s=5.0, gate=gate)

    with pytest.raises(redis.exceptions.ConnectionError):
        await anext(stream)

    assert gate.active == 0


async def test_gate_releases_when_aclose_raises_after_a_normal_loop_exit() -> None:
    gate = StreamGate(limit=1)
    assert gate.acquire() is True
    fake_redis = _RedisPubSubReturns(_SubscribeOkThenAcloseRaisesPubSub())
    stream = verdict_event_stream(fake_redis, heartbeat_s=5.0, gate=gate)

    with pytest.raises(redis.exceptions.ConnectionError):
        await anext(stream)

    assert gate.active == 0


async def test_bad_payload_is_logged_and_skipped_then_streaming_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    good_message = _valid_message()
    pubsub = _ScriptedPubSub([{"type": "message", "data": b"{"}, good_message])
    fake_redis = _RedisPubSubReturns(pubsub)

    with caplog.at_level(logging.WARNING):
        stream = verdict_event_stream(fake_redis, heartbeat_s=5.0)
        frame = await anext(stream)
        await stream.aclose()

    # Exactly one frame: the bad message was skipped, not yielded as "" or garbage.
    assert frame == render_verdict_event(good_message["data"])

    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "JSONDecodeError" in warnings[0]
    # Never the payload bytes, never a URL (PRD §10.6) — just the exception's class name.
    assert "{" not in warnings[0]
