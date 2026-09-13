"""Pins `GET /api/v1/stream` end to end: real Redis pub/sub through a REAL ASGI server
(PRD §8 `/stream` row; m8a task-01, Interfaces → test table).

**Why a real `uvicorn.Server`, not `httpx.ASGITransport`:** `ASGITransport.handle_async_request`
(httpx 0.28.1) `await`s the whole ASGI app coroutine to completion before returning ANY response
to httpx — even just the status/headers — because it collects every `http.response.body` chunk
into a list inside that single `await`. An endless `StreamingResponse` (our heartbeat-forever
stream) therefore hangs `client.stream()` exactly as badly as a plain `client.get` would; there is
no way to observe a partial body while the generator is still running (verified empirically while
authoring this file — see the test-author report's Judgment calls). A real `uvicorn.Server` on a
real 127.0.0.1 socket streams over an actual TCP connection, exactly like production, and
correctly detects a client disconnect through Starlette's own disconnect handling.

A plain `client.get(...)` is still never used on `STREAM_PATH` when a 200 is possible: `.get()`
always reads the FULL body before returning, and our body never ends (task-01 brief, Step 1).
Every blocking read is wrapped in `asyncio.wait_for(..., 5)` so a regression fails the test
instead of hanging CI. Redis pub/sub has no backlog, so `_wait_for_one_subscriber` polls
`arq_redis.pubsub_numsub` until the route's own subscription has actually landed before this test
publishes — otherwise the message is dropped and the test would hang until its `wait_for`
deadline (briefing ruling 2).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import socket
import subprocess
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from arq.connections import ArqRedis
from fastapi import FastAPI
from httpx import AsyncClient, Response

import api.routes.stream as stream_module
from api.factory import create_app
from api.routes.stream import VERDICT_CREATED_EVENT
from core.config import Settings
from core.queue import VERDICT_CREATED_CHANNEL
from core.schemas.alerts_read import reasoning_excerpt
from core.schemas.verdict import Verdict
from worker.publish import publish_verdict_created

STREAM_PATH = "/api/v1/stream"


def _verdict() -> Verdict:
    return Verdict(
        severity=3,
        category="scanning",
        confidence=0.7,
        reasoning="repeated port scans from a known scanner range, no follow-up observed",
        recommended_action="continue monitoring",
        escalate=False,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def _running_app(app: FastAPI) -> AsyncIterator[str]:
    """Serve `app` on a real 127.0.0.1 TCP socket for the `with` block's lifetime, yielding its
    base URL (see the module docstring for why `httpx.ASGITransport` cannot do this)."""
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
    """Poll until exactly one subscriber is on `VERDICT_CREATED_CHANNEL`. The caller wraps this
    in `asyncio.wait_for(..., 5)` — Redis pub/sub has no backlog, so publishing before the
    subscription lands drops the message (task-01 briefing ruling 2)."""
    channel = VERDICT_CREATED_CHANNEL.encode()
    while True:
        counts = dict(await redis.pubsub_numsub(VERDICT_CREATED_CHANNEL))
        if counts.get(channel, 0) >= 1:
            return
        await asyncio.sleep(0.02)


async def _read_verdict_frame(response: Response) -> tuple[str, str]:
    """Read SSE lines off `response` until a `verdict.created` frame's `event:`/`data:` pair is
    seen, skipping any heartbeat comment lines (`: heartbeat`) along the way."""
    event_line = ""
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            event_line = line
            continue
        if line.startswith("data: ") and event_line == f"event: {VERDICT_CREATED_EVENT}":
            return event_line, line
    raise AssertionError("stream ended before a verdict.created frame arrived")


async def test_stream_delivers_a_published_verdict_event(
    arq_redis: ArqRedis, settings: Settings
) -> None:
    app = create_app(redis=arq_redis, settings=settings)
    alert_id = uuid.uuid4()
    verdict_id = uuid.uuid4()
    verdict = _verdict()

    async with _running_app(app) as base_url, AsyncClient(base_url=base_url) as client:
        async with client.stream("GET", STREAM_PATH) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            # Both proxy-buffering headers named verbatim in the Interfaces block's
            # `SSE_HEADERS` (review I2): `no-transform` stops an intermediary from gzip-ing and
            # buffering the stream, and `X-Accel-Buffering: no` is what stops an nginx-style
            # reverse proxy from buffering the response — losing either is invisible in dev/CI
            # and only shows up as "the dashboard never updates" behind a real proxy.
            assert "no-cache" in response.headers["cache-control"]
            assert "no-transform" in response.headers["cache-control"]
            assert response.headers["x-accel-buffering"] == "no"

            await asyncio.wait_for(_wait_for_one_subscriber(arq_redis), 5)
            await publish_verdict_created(
                arq_redis, alert_id=alert_id, verdict_id=verdict_id, verdict=verdict
            )

            event_line, data_line = await asyncio.wait_for(_read_verdict_frame(response), 5)

    assert event_line == f"event: {VERDICT_CREATED_EVENT}"
    payload = json.loads(data_line[len("data: ") :])
    assert payload == {
        "alert_id": str(alert_id),
        "verdict_id": str(verdict_id),
        "severity": verdict.severity,
        "category": verdict.category,
        "escalate": verdict.escalate,
        "summary": reasoning_excerpt(verdict.reasoning),
    }


async def test_stream_503_envelope_when_redis_is_unwired() -> None:
    app = create_app()

    async with _running_app(app) as base_url, AsyncClient(base_url=base_url) as client:
        response = await client.get(STREAM_PATH)

    assert response.status_code == 503
    # >= 500 responses never carry the real message on the wire (api/errors.py's own hardening,
    # M2 final review defect 3) — only the log line would; the wire `code` still identifies it.
    assert response.json() == {"error": {"code": "stream_unavailable", "message": "internal error"}}


async def test_stream_429_envelope_when_the_client_cap_is_reached(arq_redis: ArqRedis) -> None:
    # A short heartbeat so the server's next write attempt (and therefore its disconnect check)
    # happens quickly once the first connection closes — the gate's release timing depends on
    # the app's own next `send()`, not on the client merely closing its socket (see the
    # test-author report's Judgment calls for the empirical trace).
    settings = Settings(stream_max_clients=1, stream_heartbeat_s=0.2)
    app = create_app(redis=arq_redis, settings=settings)

    async with _running_app(app) as base_url:
        async with AsyncClient(base_url=base_url) as first_client:
            async with first_client.stream("GET", STREAM_PATH) as first:
                assert first.status_code == 200
                await asyncio.wait_for(_wait_for_one_subscriber(arq_redis), 5)

                second = await first_client.get(STREAM_PATH)
                assert second.status_code == 429
                assert second.json()["error"]["code"] == "rate_limited"

        # `first_client` is closed now. Retry through the real HTTP surface (never a plain
        # `.get()`, which would hang once a slot is free and the response is 200) until the gate
        # accepts a new client again.
        async def _retry_until_free() -> None:
            async with AsyncClient(base_url=base_url) as client:
                while True:
                    async with client.stream("GET", STREAM_PATH) as response:
                        if response.status_code == 200:
                            return
                    await asyncio.sleep(0.05)

        await asyncio.wait_for(_retry_until_free(), 5)


async def test_stream_sends_access_control_allow_origin_for_a_configured_origin(
    arq_redis: ArqRedis, settings: Settings
) -> None:
    app = create_app(redis=arq_redis, settings=settings)

    async with _running_app(app) as base_url, AsyncClient(base_url=base_url) as client:
        async with client.stream(
            "GET", STREAM_PATH, headers={"Origin": "http://localhost:3000"}
        ) as response:
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


async def test_openapi_declares_the_stream_operation_with_event_stream_content() -> None:
    spec = create_app().openapi()

    operations = [
        operation
        for path_item in spec["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict)
    ]
    stream_operations = [op for op in operations if op.get("operationId") == "stream_verdicts"]
    assert len(stream_operations) == 1
    operation = stream_operations[0]

    responses = operation["responses"]
    assert "text/event-stream" in responses["200"]["content"]
    for status in ("429", "503"):
        ref = responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref == "#/components/schemas/ErrorEnvelope"


def test_stream_module_imports_no_worker_or_llm() -> None:
    source = inspect.getsource(stream_module)

    assert "import worker" not in source
    assert "from worker" not in source
    assert "core.llm" not in source

    proc = subprocess.run(["uv", "run", "lint-imports"], capture_output=True, text=True)
    assert proc.returncode == 0, f"lint-imports failed:\n{proc.stdout}\n{proc.stderr}"
