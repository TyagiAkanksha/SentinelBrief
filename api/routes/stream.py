"""`GET /api/v1/stream` — Server-Sent Events fed by the `verdict.created` Redis pub/sub channel
(PRD §8 `/stream` row, §9 page 1, §10.1, §10.6) — m8a task-01.

Two properties carry the security weight of this module:

1. `render_verdict_event` is the ONLY place a raw channel message becomes wire bytes. It
   validates the message through `VerdictCreatedEvent` and serializes it with
   `model_dump_json()` — never string interpolation — so a `summary` containing a newline
   (attacker-influenced model text, PRD §10.6) can never start a second, forged SSE event.
2. `StreamGate` bounds concurrent clients (`Settings.stream_max_clients`): each holds one HTTP
   connection and one Redis pub/sub connection. The route acquires; `verdict_event_stream`'s
   generator releases in its `finally` — Starlette always closes a `StreamingResponse`'s
   generator (normal end, client disconnect, or shutdown), so every acquire gets exactly one
   release.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from api.deps import RedisDep, get_settings
from core.config import Settings
from core.errors import RateLimitedError
from core.queue import VERDICT_CREATED_CHANNEL
from core.schemas.errors import ErrorEnvelope
from core.schemas.stream import VerdictCreatedEvent

logger = logging.getLogger(__name__)

router = APIRouter()

HEARTBEAT_COMMENT = "heartbeat"
VERDICT_CREATED_EVENT = "verdict.created"
"""The SSE `event:` name the browser's `useAlertStream` hook listens for."""
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
"""`X-Accel-Buffering: no` disables any reverse-proxy response buffering (nginx-style proxies)
so heartbeats and events reach the browser as soon as they are written."""


def format_sse(event: str, data: str) -> str:
    """Build one named SSE frame: `event: {event}\\ndata: {data}\\n\\n`.

    Args:
        event: The SSE `event:` name.
        data: The frame's `data:` payload — already-serialized text, never interpolated raw
            attacker-influenced content (see `render_verdict_event`).

    Returns:
        The frame text, terminated by a blank line (the SSE frame separator).
    """
    return f"event: {event}\ndata: {data}\n\n"


def format_comment(text: str) -> str:
    """Build one SSE comment: `: {text}\\n\\n` — invisible to `EventSource` listeners, keeps the
    connection warm through idle proxies and load balancers.

    Args:
        text: The comment text.

    Returns:
        The comment, terminated by a blank line.
    """
    return f": {text}\n\n"


def render_verdict_event(raw: str | bytes) -> str | None:
    """Turn one raw `verdict.created` channel message into an SSE frame, or `None` if it is not
    a valid `VerdictCreatedEvent`.

    This is the ONLY place a channel message becomes wire bytes: `model_dump_json()` escapes
    `\\n`/`\\r` inside `summary` (PRD §10.6) to the two-character sequences `\\n`/`\\r` — never a
    raw newline byte — so one message is always exactly one physical `data:` line.

    Args:
        raw: The raw `message["data"]` value from `pubsub.get_message` — `str` or `bytes`.

    Returns:
        The rendered SSE frame, or `None` on `json.JSONDecodeError`, `UnicodeDecodeError`, or a
        `pydantic.ValidationError` — the caller logs a WARNING naming the exception CLASS only,
        never the payload (PRD §10.6: the payload may embed attacker-influenced text).
    """
    try:
        data = json.loads(raw)
        event = VerdictCreatedEvent.model_validate(data)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        logger.warning("verdict.created message dropped exc=%s", type(exc).__name__)
        return None
    return format_sse(VERDICT_CREATED_EVENT, event.model_dump_json())


@dataclass
class StreamGate:
    """Bounds concurrent SSE clients: one HTTP + one Redis pub/sub connection each.

    PAIRING CONTRACT: the ROUTE acquires; the GENERATOR (`verdict_event_stream`) releases in its
    `finally`. Starlette always closes a `StreamingResponse`'s generator — on normal end, on
    client disconnect, and on shutdown — so every acquire has exactly one release.
    """

    limit: int
    active: int = 0

    def acquire(self) -> bool:
        """Claim one slot.

        Returns:
            `False` when `active >= limit` (no slot claimed); otherwise increments `active` and
            returns `True`.
        """
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        """Release one slot, clamped at zero so an extra release never goes negative."""
        self.active = max(0, self.active - 1)


async def verdict_event_stream(
    redis: Redis, *, heartbeat_s: float, gate: StreamGate | None = None
) -> AsyncIterator[str]:
    """Subscribe to `VERDICT_CREATED_CHANNEL` and yield an SSE frame per message, heartbeating
    while idle.

    Reuses the shared `redis` client rather than opening a second one with its own socket
    timeout: `redis`'s socket_timeout (`Settings.redis_socket_timeout_s`) can fire as a
    `redis.exceptions.TimeoutError` before `timeout=heartbeat_s` does, so the effective heartbeat
    is `min(heartbeat_s, redis_socket_timeout_s)` — harmless, since a quiet channel must never
    end the stream (briefing ruling R1).

    Args:
        redis: The Redis client to subscribe through. Named `redis`, which SHADOWS the
            top-level `redis` package inside this function body — the module-level
            `RedisTimeoutError`/`RedisError` imports exist so this function never writes
            `redis.exceptions.X`, which would raise `AttributeError` at runtime (briefing
            ruling R14).
        heartbeat_s: `pubsub.get_message`'s `timeout`; also the idle interval a `None` result
            yields a heartbeat at.
        gate: The `StreamGate` to release in this generator's `finally`, or `None` in tests that
            do not exercise the client cap.

    Yields:
        One SSE frame (`format_comment(HEARTBEAT_COMMENT)`, or `render_verdict_event`'s result)
        per loop iteration.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(VERDICT_CREATED_CHANNEL)
    try:
        try:
            while True:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=heartbeat_s
                    )
                except RedisTimeoutError:
                    yield format_comment(HEARTBEAT_COMMENT)
                    continue
                except (RedisError, OSError) as exc:
                    logger.warning("verdict event stream ended exc=%s", type(exc).__name__)
                    return

                if message is None:
                    yield format_comment(HEARTBEAT_COMMENT)
                    continue

                rendered = render_verdict_event(message["data"])
                if rendered is None:
                    continue
                yield rendered
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py's PubSub.aclose has no return annotation upstream
    finally:
        if gate is not None:
            gate.release()


@router.get(
    "/stream",
    operation_id="stream_verdicts",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE stream of verdict.created events.",
        },
        429: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
async def stream_verdicts(
    request: Request, redis: RedisDep, settings: Settings = Depends(get_settings)
) -> StreamingResponse:
    """Stream `verdict.created` events as SSE, bounded by `Settings.stream_max_clients`.
    \f
    No `try`/`except` here (`.claude/rules/api.md`): the gate slot this route acquires is
    released by `verdict_event_stream`'s generator, not by this route body.

    Args:
        request: The current request, used to reach `app.state.stream_gate`.
        redis: The wired Redis client (`RedisDep`; raises `StreamUnavailableError` -> 503 when
            unwired).
        settings: The app's `Settings`, for `stream_heartbeat_s`.

    Returns:
        A `text/event-stream` `StreamingResponse`.

    Raises:
        RateLimitedError: When `Settings.stream_max_clients` concurrent streams are already
            open (mapped to 429).
    """
    gate: StreamGate = request.app.state.stream_gate
    if not gate.acquire():
        raise RateLimitedError("too many concurrent stream clients")
    return StreamingResponse(
        verdict_event_stream(redis, heartbeat_s=settings.stream_heartbeat_s, gate=gate),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
