"""`GET /api/v1/alerts`, `/alerts/{id}`, `/stats` — the unsigned public read router
(PRD §8, §10.1) — m3 task-02.

Public GET paths never compute (PRD §10.1): every route here answers from the task-01 read
services and the database alone, never the LLM. `router` is a plain `APIRouter()` — never
`route_class=SignedRoute` — because `api/routes/alerts.py::SignedRoute`'s signature gate is for
the one ingest `POST` only. List and stats responses are served through `_cached_json`, an
in-process TTL cache seam (`core/cache.py`) keyed on the route's own declared query params
(`cache_key`), never the raw request query string — an undeclared query param (e.g. `?zzz=1`)
can therefore never mint a new cache entry (M2/M3 review, plan finding I3: a public GET must not
be an unbounded resource sink, PRD §10.1). Only a successful `produce()` ever reaches `cache.set`,
so a non-2xx response is never cached by construction. The detail route is never cached (PRD §8
caches list and stats only).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis

from api.deps import SessionDep, get_cache, get_redis_optional, get_settings
from core.cache import TTLCache
from core.config import Settings
from core.schemas.alerts_read import AlertDetail, AlertSummary, ListFilters, StatsOut
from core.schemas.errors import ErrorEnvelope
from core.schemas.pagination import PaginatedResponse
from core.schemas.verdict import VerdictCategory
from core.services.alerts_read import get_alert_detail
from core.services.alerts_read import get_stats as get_stats_service
from core.services.alerts_read import list_alerts as list_alerts_service

router = APIRouter()


def cache_key(path: str, params: Mapping[str, object]) -> str:
    """The cache key for `path`: only its caller-declared params, sorted by name and urlencoded
    — never the raw request query string, so an undeclared query param can never mint a new
    cache entry (M2/M3 review, plan finding I3).

    `None` values are dropped (an unset filter); `bool` becomes `"true"`/`"false"` (checked
    before `int`, since `bool` is an `int` subclass); `datetime` becomes its ISO 8601 form;
    everything else is `str()`.
    """
    pairs: list[tuple[str, str]] = []
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        elif isinstance(value, datetime):
            pairs.append((name, value.isoformat()))
        else:
            pairs.append((name, str(value)))
    pairs.sort()
    if not pairs:
        return path
    return f"{path}?{urlencode(pairs)}"


async def _cached_json(
    request: Request,
    cache: TTLCache,
    ttl_s: int,
    produce: Callable[[], Awaitable[BaseModel]],
    params: Mapping[str, object],
) -> Response:
    """Serve `request` from `cache`, computing and caching it via `produce` on a miss.

    Args:
        request: The current request; `request.url.path` plus `params` form the cache key
            (`cache_key`).
        cache: The wired `TTLCache`.
        ttl_s: Seconds the produced response stays cached.
        produce: Awaited only on a cache miss; its result is JSON-serialized and cached.
        params: The route's own declared query params (never the raw request query string).

    Returns:
        A `Response` with `media_type="application/json"`, either the cached bytes or the
        freshly produced ones. `cache.set` is only ever reached after a successful `produce()`,
        so a non-2xx response is never cached.
    """
    key = cache_key(request.url.path, params)
    hit = await cache.get(key)
    if hit is not None:
        return Response(content=hit, media_type="application/json")

    model = await produce()
    body = model.model_dump_json().encode()
    await cache.set(key, body, ttl_s)
    return Response(content=body, media_type="application/json")


@router.get(
    "/alerts",
    operation_id="list_alerts",
    response_model=PaginatedResponse[AlertSummary],
    responses={422: {"model": ErrorEnvelope}, 500: {"model": ErrorEnvelope}},
)
async def list_alerts(
    request: Request,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
    cache: TTLCache = Depends(get_cache),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    severity_gte: int | None = Query(None, ge=1, le=5),
    category: VerdictCategory | None = Query(None),
    since: datetime | None = Query(None),
    escalate: bool | None = Query(None),
) -> Response:
    """Page the alert list, latest verdict per alert, cached for `ALERTS_LIST_CACHE_TTL_S`.
    \f
    Args:
        request: The current request; used only for its path (the cache key).
        session: The request-scoped session (`SessionDep`).
        settings: The app's `Settings`, for `alerts_list_cache_ttl_s`.
        cache: The wired `TTLCache`.
        page: 1-indexed page number.
        page_size: Rows per page, capped at 100.
        severity_gte: Only alerts whose latest verdict's severity is at least this.
        category: Only alerts whose latest verdict has this category.
        since: Only alerts received at or after this timestamp.
        escalate: Only alerts whose latest verdict's `escalate` matches this.

    Returns:
        A `PaginatedResponse[AlertSummary]` JSON body, from the cache on a hit.
    """
    filters = ListFilters(
        severity_gte=severity_gte, category=category, since=since, escalate=escalate
    )

    async def _produce() -> PaginatedResponse[AlertSummary]:
        items, total = await list_alerts_service(
            session, filters=filters, page=page, page_size=page_size
        )
        return PaginatedResponse[AlertSummary](
            items=items, total=total, page=page, page_size=page_size
        )

    params: dict[str, object] = {
        "page": page,
        "page_size": page_size,
        "severity_gte": severity_gte,
        "category": category,
        "since": since,
        "escalate": escalate,
    }
    return await _cached_json(request, cache, settings.alerts_list_cache_ttl_s, _produce, params)


@router.get(
    "/alerts/{alert_id}",
    operation_id="get_alert",
    response_model=AlertDetail,
    responses={
        404: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        500: {"model": ErrorEnvelope},
    },
)
async def get_alert(alert_id: uuid.UUID, session: SessionDep) -> AlertDetail:
    """Return `alert_id`'s full detail: raw payload, latest verdict, and its tool calls.
    \f
    Never cached (PRD §8 caches list and stats only) — a retriage is visible immediately.

    Args:
        alert_id: The alert's primary key.
        session: The request-scoped session (`SessionDep`).

    Returns:
        The alert's `AlertDetail`.

    Raises:
        NotFoundError: When no alert with `alert_id` exists (mapped to 404).
    """
    return await get_alert_detail(session, alert_id)


@router.get(
    "/stats",
    operation_id="get_stats",
    response_model=StatsOut,
    responses={500: {"model": ErrorEnvelope}},
)
async def get_stats(
    request: Request,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
    cache: TTLCache = Depends(get_cache),
    redis: Redis | None = Depends(get_redis_optional),
) -> Response:
    """The whole-database dashboard stats view, cached for `STATS_CACHE_TTL_S`.
    \f
    Args:
        request: The current request; used only for its path (the cache key).
        session: The request-scoped session (`SessionDep`).
        settings: The app's `Settings`, for `stats_cache_ttl_s`/`daily_token_budget`.
        cache: The wired `TTLCache`.
        redis: The app's Redis client, or `None` when unwired (`get_redis_optional` never 503s
            this route — m8b task-05).

    Returns:
        A `StatsOut` JSON body, from the cache on a hit.
    """

    async def _produce() -> StatsOut:
        return await get_stats_service(
            session, redis=redis, daily_token_budget=settings.daily_token_budget
        )

    return await _cached_json(request, cache, settings.stats_cache_ttl_s, _produce, {})
