"""Request-scoped seams read off `app.state` (CONVENTIONS.md §5).

Routes never read `app.state` or `os.environ` directly — they depend on `get_settings`,
`get_session` and `get_enqueue`, which are the only places that know how those seams are wired.
Nothing here imports `worker` or `core.llm` (PRD §10.1): `EnqueueFn` is a plain callable type
alias over `uuid.UUID`, never a `worker` type — the route layer only ever sees the queue seam
(m5 task-01; the LLM call now happens entirely in the worker process).
"""

from __future__ import annotations

import hmac
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import TTLCache
from core.config import Settings
from core.errors import (
    LengthRequiredError,
    PayloadTooLargeError,
    RateLimitedError,
    SignatureError,
    StreamUnavailableError,
)
from core.signing import SIGNATURE_HEADER, verify_signature

# `enqueue(alert_id)` is fire-and-forget from the route's point of view — it either queues the
# job or raises `QueueUnavailableError`; it never returns/persists a status itself (m5 task-01).
EnqueueFn = Callable[[uuid.UUID], Awaitable[None]]


def get_settings(request: Request) -> Settings:
    """Return the app's `Settings`, defaulting to a zero-env `Settings()` when none is wired.

    Args:
        request: The current request, used to reach `app.state`.

    Returns:
        The `Settings` instance `create_app()` was built with, or a fresh default one.
    """
    settings: Settings | None = request.app.state.settings
    if settings is None:
        return Settings()
    return settings


def get_cache(request: Request) -> TTLCache:
    """Return the app's `TTLCache`, always installed by `create_app()`.

    Args:
        request: The current request, used to reach `app.state.cache`.

    Returns:
        The `TTLCache` instance `create_app()` was built with (or its default
        `InMemoryTTLCache` when no `cache=` kwarg was passed).
    """
    cache: TTLCache = request.app.state.cache
    return cache


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the wired `session_factory`, owning commit/rollback.

    Args:
        request: The current request, used to reach `app.state.session_factory`.

    Yields:
        An `AsyncSession` for the route to use.

    Raises:
        RuntimeError: When no `session_factory` was wired into `create_app()`.
    """
    factory = request.app.state.session_factory
    if factory is None:
        raise RuntimeError("no session_factory wired")
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Routes take `session: SessionDep`, never a bare `Depends(get_session)`. FastAPI's default
# dependency scope for a yield-dependency is `"request"`, which runs the code after `yield`
# (the commit/rollback here) only after the response has already been sent to the client — a
# failing `commit()` would then be swallowed and the client would see the route handler's `200`
# for a write that never persisted. `scope="function"` runs that exit code **before** the
# response is built, so a failing commit propagates through `register_error_handlers` as the
# generic 500 envelope instead (CONVENTIONS.md §3, §5).
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_enqueue(request: Request) -> EnqueueFn:
    """Return the wired enqueue callable (m5 task-01: the route layer's only queue seam).

    Args:
        request: The current request, used to reach `app.state.enqueue`.

    Returns:
        The `EnqueueFn` `create_app()` was built with.

    Raises:
        RuntimeError: When no `enqueue` was wired into `create_app()`.
    """
    enqueue: EnqueueFn | None = request.app.state.enqueue
    if enqueue is None:
        raise RuntimeError("no enqueue wired")
    return enqueue


def require_content_length(request: Request, settings: Settings) -> None:
    """Raise before a signed-route request's body is ever read, when its declared length is bad.

    Reads only the `Content-Length` header — never `receive`/the body itself — so this check is
    genuinely free: a request that fails it costs the api nothing beyond the header parse (m6
    task-02, Global Constraint "Bound the ingest request body").

    Args:
        request: The current request; only its `content-length` header is read.
        settings: The app's `Settings`, for `ingest_max_body_bytes`.

    Raises:
        LengthRequiredError: No `Content-Length` header, or its value is not all digits (e.g. a
            chunked request, which carries `Transfer-Encoding` instead).
        PayloadTooLargeError: The declared length exceeds `settings.ingest_max_body_bytes`.
    """
    header = request.headers.get("content-length")
    if header is None or not header.isdigit():
        raise LengthRequiredError("Content-Length required")
    if int(header) > settings.ingest_max_body_bytes:
        raise PayloadTooLargeError(f"body exceeds {settings.ingest_max_body_bytes} bytes")


def get_redis(request: Request) -> Redis:
    """Return the app's wired Redis client, for the `/stream` route's pub/sub subscription.

    Args:
        request: The current request, used to reach `app.state.redis`.

    Returns:
        The `Redis` client `create_app()` was built with.

    Raises:
        StreamUnavailableError: When no `redis` was wired into `create_app()` — a 503 the
            dashboard can render, never a bare `RuntimeError` (m8a task-01).
    """
    redis: Redis | None = request.app.state.redis
    if redis is None:
        raise StreamUnavailableError("event stream unavailable")
    return redis


RedisDep = Annotated[Redis, Depends(get_redis)]


def get_redis_optional(request: Request) -> Redis | None:
    """Return the app's wired Redis client, or `None` when unwired — never raises.

    Unlike `get_redis`/`RedisDep` (which 503s the SSE stream route on an unwired Redis),
    `GET /api/v1/stats` must keep answering `200` with `budget_exhausted=False`/`tokens_today=0`
    even when no Redis is wired (m8b task-05; mirrors `rate_limit`'s own fail-open-on-unwired-
    Redis contract, m8b task-04).

    Args:
        request: The current request, used to reach `app.state.redis`.

    Returns:
        The wired `Redis` client, or `None`.
    """
    redis: Redis | None = request.app.state.redis
    return redis


async def require_signature(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Raise `SignatureError` unless the raw request body carries a valid `X-Signature`.

    Reads the raw body itself, rather than depending on the parsed body model, so the signature
    check never depends on the body being valid JSON. `api/routes/alerts.py::SignedRoute` calls
    this directly, ahead of FastAPI's own body parsing — which is what actually guarantees
    401-before-422 for malformed JSON, since FastAPI decodes the JSON body before solving
    `Depends` and a bare `Depends(require_signature)` alone could not do it. This is the one
    function that verifies the signature; `tests/test_require_signature.py` pins it directly.

    Args:
        request: The current request; used to read the raw body and the `X-Signature` header.
        settings: The app's `Settings`, for `ingest_hmac_secret`.

    Raises:
        SignatureError: When the signature is missing or does not verify.
    """
    body = await request.body()
    header = request.headers.get(SIGNATURE_HEADER)
    if not verify_signature(settings.ingest_hmac_secret.get_secret_value(), body, header):
        raise SignatureError("missing or invalid signature")


async def rate_limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Enforce the per-client-IP public GET rate limit (PRD §8, §10.10; m8b task-04, ruling
    R-M8b-1).

    A Redis fixed-window counter keyed `f"rl:{client_ip}:{minute_bucket}"`, incremented and given
    a ~60 s expiry in one pipelined round trip. `client_ip` is the ASGI peer address
    (`request.client.host`) ONLY — `X-Forwarded-For` is never read, so a forged header can never
    mint a second bucket (Caddy overwrites it and the api is never host-published).

    Deliberately reads `request.app.state.redis` directly rather than depending on
    `api.deps.get_redis` (ruling R-M8b-1): `get_redis` raises `StreamUnavailableError` -> 503
    whenever Redis is unwired, which would 503 every DB-less/no-Redis test of the public read
    router (`tests/test_read_routes.py`, pinned, never wires `redis=`). This function instead
    treats "unwired" exactly like "unreachable": both fail OPEN (allow the request, no limiting),
    never 500/503 a public GET.

    Args:
        request: The current request; used for `app.state.redis` and the peer address.
        settings: The app's `Settings`, for `public_rate_limit_per_min`.

    Raises:
        RateLimitedError: When the bucket's count exceeds `public_rate_limit_per_min`, carrying
            `retry_after` (seconds to the next minute boundary) — `api/errors.py` turns that into
            a `Retry-After` response header.
    """
    if settings.public_rate_limit_per_min <= 0:
        return
    redis: Redis | None = request.app.state.redis
    if redis is None:
        return
    client = request.client
    if client is None:
        return
    now = time.time()
    minute_bucket = int(now) // 60
    key = f"rl:{client.host}:{minute_bucket}"
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)
        count, _ = await pipe.execute()
    except (RedisError, OSError):
        return
    if count > settings.public_rate_limit_per_min:
        retry_after = 60 - int(now) % 60
        raise RateLimitedError("public rate limit exceeded", retry_after=retry_after)


async def require_admin_token(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Raise `SignatureError` unless `Authorization: Bearer <ADMIN_TOKEN>` matches exactly (PRD
    §10; m8b task-04).

    An empty `settings.admin_token` (the default, unconfigured) always 401s, even against an
    empty bearer — never fail-open just because both sides compare equal-empty. The comparison
    itself is `hmac.compare_digest`, a constant-time compare over the two token strings.

    Args:
        request: The current request; only its `authorization` header is read.
        settings: The app's `Settings`, for `admin_token`.

    Raises:
        SignatureError: When the header is missing, malformed, or does not carry the exact
            configured bearer token (mapped to a 401 `unauthorized` envelope).
    """
    configured = settings.admin_token.get_secret_value()
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if not configured or scheme != "Bearer" or not hmac.compare_digest(token, configured):
        raise SignatureError("missing or invalid admin token")
