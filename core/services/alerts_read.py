"""Read-only, session-first services answering PRD §8 from the database alone (m3 task-01).

`list_alerts`, `get_alert_detail`, and `get_stats` never `commit()` (CONVENTIONS.md §3) and never
trigger compute — they only ever read `alerts`, `verdicts`, and `tool_calls`. Every "latest
verdict per alert" view (list rows, detail, and the stats distributions) shares one `DISTINCT ON`
subquery so a retriaged alert always collapses to its newest verdict, never both.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast, get_args

from redis.asyncio import Redis
from sqlalchemy import Date, Subquery, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession

from core.budget import read_tokens_today
from core.models import AlertRow, AlertStatus, ToolCallRow, VerdictRow
from core.schemas.alerts_read import (
    AlertDetail,
    AlertSummary,
    DayCost,
    DayVolume,
    ListFilters,
    StatsOut,
    ToolCallOut,
    VerdictOut,
    VerdictSummary,
    normalize_country,
    reasoning_excerpt,
)
from core.schemas.verdict import VerdictCategory
from core.services.alerts import get_alert

_SIX_DP = Decimal("0.000001")

GEO_TOOL_NAME = "get_ip_geo_asn"


def latest_verdicts_subquery() -> Subquery:
    """The latest verdict per alert: Postgres `DISTINCT ON (alert_id)` (PRD §5, §9).

    Ordered `alert_id, created_at DESC, id DESC` — the `id` tiebreaker matters when two verdicts
    for the same alert share a `created_at` (e.g. two writes in one transaction/second). Public —
    shared with alert_history (m4 task-05).
    """
    return (
        select(VerdictRow)
        .distinct(VerdictRow.alert_id)
        .order_by(VerdictRow.alert_id, VerdictRow.created_at.desc(), VerdictRow.id.desc())
        .subquery("latest")
    )


def geo_country_subquery() -> Subquery:
    """The first `GEO_TOOL_NAME` call's `result["country"]` per verdict (PRD §9).

    `DISTINCT ON (verdict_id) ORDER BY verdict_id, seq` picks the earliest such call when a
    verdict's tool loop invoked the geo tool more than once; `->>` yields SQL `NULL` for an
    `{unavailable}` result (no `"country"` key), left to `normalize_country` in Python.
    """
    return (
        select(ToolCallRow.verdict_id, ToolCallRow.result["country"].astext.label("country"))
        .where(ToolCallRow.tool_name == GEO_TOOL_NAME)
        .distinct(ToolCallRow.verdict_id)
        .order_by(ToolCallRow.verdict_id, ToolCallRow.seq)
        .subquery("geo")
    )


async def list_alerts(
    session: AsyncSession, *, filters: ListFilters, page: int, page_size: int
) -> tuple[list[AlertSummary], int]:
    """Page the alert list, latest verdict per alert, ordered latest-first (PRD §9).

    Ordered `severity DESC NULLS LAST, received_at DESC` then `id DESC` so pending/failed alerts
    (`verdict=None`) still list, after every verdict-bearing row, and paging never drops or
    duplicates a row.

    Args:
        session: The request-scoped `AsyncSession`.
        filters: PRD §8 list filters (`severity_gte`, `category`, `since`, `escalate`); a
            verdict-field filter naturally excludes alerts with no verdict (`NULL` comparisons).
        page: 1-indexed page number.
        page_size: Rows per page.

    Returns:
        The page's `AlertSummary` rows and the total count of rows matching `filters`.
    """
    latest = latest_verdicts_subquery()
    geo = geo_country_subquery()

    # `geo` is joined only into `items_stmt`, never into `base`/the COUNT statement: it cannot
    # change the row count (1:0..1 on `latest.id`), so joining it there would only add an unused
    # `tool_calls` scan to the dashboard's hot public path (m4 task-07 fix-1 M2).
    base = select(
        AlertRow.id,
        AlertRow.source,
        func.coalesce(AlertRow.raw["src_ip"].astext, "").label("src_ip"),
        func.coalesce(AlertRow.raw["sensor"].astext, "").label("sensor"),
        AlertRow.event_time,
        AlertRow.received_at,
        AlertRow.status,
        latest.c.severity,
        latest.c.category,
        latest.c.confidence,
        latest.c.escalate,
        latest.c.reasoning,
        latest.c.created_at,
    ).outerjoin(latest, latest.c.alert_id == AlertRow.id)

    if filters.severity_gte is not None:
        base = base.where(latest.c.severity >= filters.severity_gte)
    if filters.category is not None:
        base = base.where(latest.c.category == filters.category)
    if filters.escalate is not None:
        base = base.where(latest.c.escalate == filters.escalate)
    if filters.since is not None:
        base = base.where(AlertRow.received_at >= filters.since)

    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    assert total is not None

    items_stmt = (
        base.add_columns(geo.c.country)
        .outerjoin(geo, geo.c.verdict_id == latest.c.id)
        .order_by(
            latest.c.severity.desc().nulls_last(),
            AlertRow.received_at.desc(),
            AlertRow.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(items_stmt)).all()

    items = [
        AlertSummary(
            id=row.id,
            source=row.source,
            src_ip=row.src_ip,
            sensor=row.sensor,
            event_time=row.event_time,
            received_at=row.received_at,
            status=cast(AlertStatus, row.status),
            country=normalize_country(row.country),
            verdict=(
                None
                if row.severity is None
                else VerdictSummary(
                    severity=row.severity,
                    category=cast(VerdictCategory, row.category),
                    confidence=row.confidence,
                    escalate=row.escalate,
                    reasoning_excerpt=reasoning_excerpt(row.reasoning),
                    created_at=row.created_at,
                )
            ),
        )
        for row in rows
    ]
    return items, total


async def get_alert_detail(session: AsyncSession, alert_id: uuid.UUID) -> AlertDetail:
    """Return `alert_id`'s full detail: raw payload, latest verdict, and tool calls (PRD §5, §8).

    Tool calls are returned in `seq` order.

    Args:
        session: The request-scoped `AsyncSession`.
        alert_id: The alert's primary key.

    Returns:
        The alert's `AlertDetail`; `verdict=None` and `tool_calls=[]` when it has never been
        triaged.

    Raises:
        NotFoundError: When no alert with `alert_id` exists.
    """
    row = await get_alert(session, alert_id)

    latest_stmt = (
        select(VerdictRow)
        .where(VerdictRow.alert_id == alert_id)
        .order_by(VerdictRow.created_at.desc(), VerdictRow.id.desc())
        .limit(1)
    )
    latest_verdict = (await session.execute(latest_stmt)).scalar_one_or_none()

    verdict_out: VerdictOut | None = None
    tool_calls: list[ToolCallOut] = []
    if latest_verdict is not None:
        verdict_out = VerdictOut.model_validate(latest_verdict)
        tool_calls_stmt = (
            select(ToolCallRow)
            .where(ToolCallRow.verdict_id == latest_verdict.id)
            .order_by(ToolCallRow.seq)
        )
        tool_call_rows = (await session.execute(tool_calls_stmt)).scalars().all()
        tool_calls = [ToolCallOut.model_validate(tc) for tc in tool_call_rows]

    raw: dict[str, Any] = row.raw
    country = normalize_country(
        next((tc.result.get("country") for tc in tool_calls if tc.tool_name == GEO_TOOL_NAME), None)
    )
    return AlertDetail(
        id=row.id,
        source=row.source,
        src_ip=raw.get("src_ip", ""),
        sensor=raw.get("sensor", ""),
        event_time=row.event_time,
        received_at=row.received_at,
        status=cast(AlertStatus, row.status),
        country=country,
        raw=raw,
        verdict=verdict_out,
        tool_calls=tool_calls,
    )


async def get_stats(
    session: AsyncSession, *, redis: Redis | None = None, daily_token_budget: int = 0
) -> StatsOut:
    """The dashboard stats view (PRD §8): zero-filled on an empty database.

    `by_severity`/`by_category`/`escalated_count` use the latest verdict per alert (the same
    `DISTINCT ON` subquery as `list_alerts`); `cost_total_usd` sums *every* verdict row —
    retriage spend already happened and counts. `latency_pNN_ms` is Postgres `percentile_disc`,
    identical in definition to `evals/scoring.py::percentile`'s nearest rank. `cost_by_day` buckets
    every verdict's spend by the *alert's* received day (R5) and counts each alert once with
    `count(DISTINCT alerts.id)`, since the LEFT JOIN multiplies a retriaged alert's row by its
    verdict count.

    `budget_exhausted`/`tokens_today` read the daily token-budget counter directly
    (`core.budget.read_tokens_today`, never `worker.budget` — `core/` must not import `worker`,
    import-linter) and fail OPEN: an unwired or unreachable `redis` answers `tokens_today=0`,
    `budget_exhausted=False` rather than 500ing this public route (PRD §10.3, from M8; mirrors
    `api/deps.py::rate_limit`'s own unwired-Redis fail-open contract, m8b task-04).

    Args:
        session: The request-scoped `AsyncSession`.
        redis: The Redis client the daily token-budget counter is stored on, or `None` when
            unwired.
        daily_token_budget: The configured `Settings.daily_token_budget`; `0` means unlimited
            (never exhausted, regardless of the counter).

    Returns:
        The whole-database `StatsOut`.
    """
    total_alerts = await session.scalar(select(func.count()).select_from(AlertRow))
    assert total_alerts is not None

    status_rows = (
        await session.execute(select(AlertRow.status, func.count()).group_by(AlertRow.status))
    ).all()
    by_status: dict[str, int] = {status: 0 for status in get_args(AlertStatus)}
    for status, count in status_rows:
        by_status[status] = count

    latest = latest_verdicts_subquery()

    severity_rows = (
        await session.execute(select(latest.c.severity, func.count()).group_by(latest.c.severity))
    ).all()
    by_severity = {str(i): 0 for i in range(1, 6)}
    for severity, count in severity_rows:
        by_severity[str(severity)] = count

    category_rows = (
        await session.execute(select(latest.c.category, func.count()).group_by(latest.c.category))
    ).all()
    by_category: dict[str, int] = {category: 0 for category in get_args(VerdictCategory)}
    for category, count in category_rows:
        by_category[category] = count

    escalated_count = await session.scalar(
        select(func.count()).select_from(latest).where(latest.c.escalate.is_(True))
    )
    assert escalated_count is not None

    day_expr = sql_cast(func.timezone("UTC", AlertRow.received_at), Date)
    volume_rows = (
        await session.execute(
            select(day_expr.label("day"), func.count()).group_by(day_expr).order_by(day_expr)
        )
    ).all()
    volume_by_day = [DayVolume(day=day, count=count) for day, count in volume_rows]

    cost_total_usd = await session.scalar(select(func.coalesce(func.sum(VerdictRow.cost_usd), 0)))
    assert cost_total_usd is not None

    cost_mean_usd = await session.scalar(select(func.coalesce(func.avg(VerdictRow.cost_usd), 0)))
    assert cost_mean_usd is not None

    latency_p50 = await session.scalar(
        select(func.percentile_disc(0.5).within_group(VerdictRow.latency_ms)).where(
            VerdictRow.latency_ms.is_not(None)
        )
    )
    latency_p95 = await session.scalar(
        select(func.percentile_disc(0.95).within_group(VerdictRow.latency_ms)).where(
            VerdictRow.latency_ms.is_not(None)
        )
    )

    last_alert_at = await session.scalar(select(func.max(AlertRow.received_at)))

    # `count(DISTINCT alerts.id)` is load-bearing: the LEFT JOIN multiplies a retriaged alert's
    # row by its verdict count, so a bare `count()` would double `alerts` and halve the mean.
    cost_by_day_rows = (
        await session.execute(
            select(
                day_expr.label("day"),
                func.count(func.distinct(AlertRow.id)).label("alerts"),
                func.coalesce(func.sum(VerdictRow.cost_usd), 0).label("cost_usd"),
            )
            .select_from(AlertRow)
            .outerjoin(VerdictRow, VerdictRow.alert_id == AlertRow.id)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()
    cost_by_day = [
        DayCost(
            day=day,
            alerts=alerts,
            cost_usd=cost_usd,
            # No `alerts > 0` guard (ruling R24): a row exists only because at least one alert
            # produced its group, so `alerts` is >= 1 by construction.
            mean_cost_usd=(cost_usd / alerts).quantize(_SIX_DP, rounding=ROUND_HALF_UP),
        )
        for day, alerts, cost_usd in cost_by_day_rows
    ]

    tokens_today = await read_tokens_today(redis)
    budget_exhausted = daily_token_budget > 0 and tokens_today >= daily_token_budget

    return StatsOut(
        total_alerts=total_alerts,
        by_status=by_status,
        by_severity=by_severity,
        by_category=by_category,
        escalated_count=escalated_count,
        volume_by_day=volume_by_day,
        cost_total_usd=cost_total_usd,
        cost_mean_usd=cost_mean_usd.quantize(_SIX_DP, rounding=ROUND_HALF_UP),
        latency_p50_ms=latency_p50 if latency_p50 is not None else 0,
        latency_p95_ms=latency_p95 if latency_p95 is not None else 0,
        last_alert_at=last_alert_at,
        cost_by_day=cost_by_day,
        budget_exhausted=budget_exhausted,
        tokens_today=tokens_today,
        daily_token_budget=daily_token_budget,
    )
