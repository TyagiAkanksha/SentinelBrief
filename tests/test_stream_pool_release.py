"""Pins the C1 fix (`.superpowers/sdd/m8-polish/m8a-final-review.md` §4 C1): a client that opens
`GET /api/v1/stream` and then disconnects must not strand its Redis pub/sub connection in the
shared `ConnectionPool`.

**Why `_in_use_connections` is the observable, not `pubsub_numsub` or `StreamGate.active`.** The
reviewer's instrumented trace shows the disconnect-driven cancellation lands inside
`PubSub.aclose()` (redis-py 5.3.1, `redis/asyncio/client.py:843-847`) *after* the socket is
already closed and unsubscribed but *before* `connection_pool.release(self.connection)` runs — so
`pubsub_numsub` returns to 0 and the gate's `active` count returns to 0 (both already pinned
green by `tests/test_stream_route.py` and `tests/test_stream_hardening.py`) while the
`Connection` object itself is stuck in the pool's `_in_use_connections` set forever (a plain
`set`, GC-proof: `redis/asyncio/connection.py:1066`). Only
`arq_redis.connection_pool._in_use_connections` can see this leak — that is the one thing every
assertion below reads.

Two pins, both from the review's fix-shape paragraph:

1. **Integration** (real `uvicorn.Server`, real dedicated Redis) — mirrors
   `tests/test_stream_route.py`'s `_running_app`/`_wait_for_one_subscriber` shape, duplicated
   here rather than imported (that file must not be modified for this task, and
   `tests/test_stream_hardening.py`'s own docstring sets the precedent: "lives here so this file
   has no dependency on that pinned file's private helpers"). Opens the stream, waits for the
   subscription to land, disconnects, then polls for the pool to empty. Today this times out with
   one stranded connection.
2. **Unit** — a fake `PubSub` whose `aclose()` suspends once (`await asyncio.sleep(0)`, mirroring
   redis-py's `wait_closed()` suspension) before recording `released = True`. The generator's
   *second* `__anext__()` call is driven inside its own `asyncio.Task`; that task is cancelled
   only once it is genuinely parked inside `aclose()`'s suspension (not merely `.cancel()`-ed
   before it ever runs, which a single un-shielded cancellation absorbs harmlessly through the
   surrounding `finally` blocks — verified empirically while authoring this file, see the
   test-author report's Judgment calls). This reproduces the exact production timing: today the
   cancellation lands inside `aclose()` and `released` never becomes `True`.
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import redis.exceptions
import uvicorn
from arq.connections import ArqRedis
from fastapi import FastAPI
from httpx import AsyncClient

from api.factory import create_app
from api.routes.stream import StreamGate, render_verdict_event, verdict_event_stream
from core.config import Settings
from core.queue import VERDICT_CREATED_CHANNEL
from core.schemas.verdict import Verdict
from worker.publish import verdict_created_payload

STREAM_PATH = "/api/v1/stream"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def _running_app(app: FastAPI) -> AsyncIterator[str]:
    """Serve `app` on a real 127.0.0.1 TCP socket for the `with` block's lifetime (duplicated
    from `tests/test_stream_route.py`'s helper of the same name — see that file's module
    docstring for why a real server, not `httpx.ASGITransport`, is required to observe a
    never-ending SSE body mid-stream)."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serve_task, 5)


async def _wait_for_one_subscriber(redis: ArqRedis) -> None:
    """Poll until exactly one subscriber is on `VERDICT_CREATED_CHANNEL` (duplicated from
    `tests/test_stream_route.py`: Redis pub/sub has no backlog, so publishing before the
    subscription lands would drop the message — not needed by this file's tests, but the
    subscription itself must land before we can observe the pool holding its connection)."""
    channel = VERDICT_CREATED_CHANNEL.encode()
    while True:
        counts = dict(await redis.pubsub_numsub(VERDICT_CREATED_CHANNEL))
        if counts.get(channel, 0) >= 1:
            return
        await asyncio.sleep(0.02)


async def _wait_for_pool_release(pool: Any) -> None:
    """Poll until the pool's `_in_use_connections` set is empty — the only observable that can
    see the stranded connection (`pubsub_numsub` and the gate's `active` count both already read
    0 while the connection object is stuck)."""
    while len(pool._in_use_connections) != 0:
        await asyncio.sleep(0.02)


async def test_disconnecting_a_stream_releases_its_pooled_redis_connection(
    arq_redis: ArqRedis,
) -> None:
    # A short heartbeat so the server's next write attempt (and therefore its disconnect check)
    # happens quickly once the client closes its socket — mirrors
    # `test_stream_route.py::test_stream_429_envelope_when_the_client_cap_is_reached`'s rationale.
    settings = Settings(stream_heartbeat_s=0.2)
    app = create_app(redis=arq_redis, settings=settings)

    async with _running_app(app) as base_url, AsyncClient(base_url=base_url) as client:
        async with client.stream("GET", STREAM_PATH) as response:
            assert response.status_code == 200
            await asyncio.wait_for(_wait_for_one_subscriber(arq_redis), 5)
        # Leaving the `async with client.stream(...)` block closes the client's TCP connection —
        # the disconnect this pin targets. `_running_app` is still serving, so the route's own
        # teardown runs against a live server exactly as it would in production.

        pool = arq_redis.connection_pool
        await asyncio.wait_for(_wait_for_pool_release(pool), 5)
        assert len(pool._in_use_connections) == 0
        # The gate already returns to 0 today (pinned by `test_stream_hardening.py`) — asserted
        # here too so this one pin distinguishes the two resources (review C1's own framing).
        assert app.state.stream_gate.active == 0


def _valid_message() -> dict[str, Any]:
    """One well-formed `{"type": "message", "data": ...}` pubsub event, built from a real
    `Verdict` through `worker.publish.verdict_created_payload` — never a hand-written dict
    (mirrors `tests/test_stream_hardening.py::_valid_message`, duplicated here)."""
    verdict = Verdict(
        severity=2,
        category="reconnaissance",
        confidence=0.6,
        reasoning="a valid verdict for the pool-release pin",
        recommended_action="continue monitoring",
        escalate=False,
    )
    payload = verdict_created_payload(
        alert_id=uuid.uuid4(), verdict_id=uuid.uuid4(), verdict=verdict
    )
    return {"type": "message", "data": json.dumps(payload).encode()}


class _RedisPubSubReturns:
    """A fake Redis client whose synchronous `pubsub()` call returns a fixed double (mirrors
    `tests/test_stream_hardening.py`'s helper of the same name, duplicated here so this file has
    no dependency on that pinned file's private helpers)."""

    def __init__(self, pubsub: object) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> object:
        return self._pubsub


class _OneMessageThenDeadPubSub:
    """A `PubSub` double: one valid message, then the connection dies — an ordinary,
    non-cancellation exit into the generator's own cleanup `finally` (the same
    `except (RedisError, OSError): ... return` branch `test_stream_hardening.py` already pins).
    `aclose()` suspends exactly once, via `await asyncio.sleep(0)`, mirroring the real redis-py
    suspension point (`Connection.disconnect()`'s `wait_closed()`) that a disconnect-driven
    cancellation lands inside — before recording `released = True`.
    """

    def __init__(self, message: dict[str, Any]) -> None:
        self._message = message
        self._calls = 0
        self.released = False

    async def subscribe(self, channel: str) -> None:
        return None

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float
    ) -> dict[str, Any] | None:
        self._calls += 1
        if self._calls == 1:
            return self._message
        raise redis.exceptions.ConnectionError("connection dead")

    async def unsubscribe(self) -> None:
        return None

    async def aclose(self) -> None:
        await asyncio.sleep(0)
        self.released = True


async def test_pool_release_completes_even_when_cancellation_lands_inside_aclose() -> None:
    gate = StreamGate(limit=1)
    assert gate.acquire() is True
    message = _valid_message()
    pubsub = _OneMessageThenDeadPubSub(message)
    fake_redis = _RedisPubSubReturns(pubsub)
    gen = verdict_event_stream(fake_redis, heartbeat_s=5.0, gate=gate)

    first = await gen.__anext__()
    assert first == render_verdict_event(message["data"])

    task = asyncio.create_task(gen.__anext__())
    # One scheduling tick is enough for this fake: the second `get_message()` call raises
    # synchronously and `unsubscribe()` never suspends, so this is exactly when the task is
    # genuinely parked inside `aclose()`'s own `await asyncio.sleep(0)` — the same point a real
    # client-disconnect cancellation lands at in production (see the module docstring).
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    async def _wait_for_release() -> None:
        while not pubsub.released:
            await asyncio.sleep(0)

    await asyncio.wait_for(_wait_for_release(), 5)
    assert pubsub.released is True
    assert gate.active == 0
