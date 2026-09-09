"""Pins `core/services/alerts_read.py`: the latest-verdict list/detail/stats read services
(PRD §5, §8, §9) — m3 task-01.

Every seeded row goes through `tests.helpers.seed_alert` / `add_verdict`, which themselves call
the production `insert_alert` / `persist_verdict` / `set_alert_status` writers, so a test fixture
here is byte-for-byte what a real ingest + triage run would produce. `ListFilters()` with no
arguments is the unfiltered query every list test starts from unless the test itself is about a
filter. The pagination-tiebreaker test is load-bearing: identical severity *and* received_at on
all 7 rows means only the `id DESC` tiebreaker keeps paging deterministic.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import get_args

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFoundError
from core.schemas.alerts_read import ListFilters, reasoning_excerpt
from core.schemas.verdict import Verdict, VerdictCategory
from core.services.alerts_read import get_alert_detail, get_stats, list_alerts
from tests.helpers import add_verdict, load_alert, seed_alert
from worker.store import ToolCallRecord

# I1: `alerts.raw` must never appear in a compiled `list_alerts` statement except immediately
# followed by ` ->>` (the JSONB extraction operator) — a whole-column selection anywhere else
# would mean the list loads full session payloads instead of projecting two scalar fields
# (verified against the actual rendered SQL — see the module docstring for `list_alerts`).
_BARE_RAW_COLUMN = re.compile(r"alerts\.raw(?!\s*->>)")


def _verdict(
    *,
    severity: int = 3,
    category: VerdictCategory = "scanning",
    escalate: bool = False,
    confidence: float = 0.7,
    reasoning: str = "test reasoning",
) -> Verdict:
    """A `Verdict` with sensible defaults, overridable per test."""
    return Verdict(
        severity=severity,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        recommended_action="monitor",
        escalate=escalate,
    )


async def test_list_alerts_orders_severity_desc_then_received_at_desc(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    id_a = await seed_alert(
        db_session,
        session_id="order-a",
        verdict=_verdict(severity=4, escalate=True),
        received_at=t0,
    )
    id_b = await seed_alert(
        db_session,
        session_id="order-b",
        verdict=_verdict(severity=4, escalate=True),
        received_at=t0 + timedelta(minutes=1),
    )
    id_c = await seed_alert(
        db_session,
        session_id="order-c",
        verdict=_verdict(severity=2),
        received_at=t0 + timedelta(minutes=2),
    )
    await db_session.commit()

    items, total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    assert [item.id for item in items] == [id_b, id_a, id_c]
    assert total == 3


async def test_list_alerts_lists_pending_and_failed_with_null_verdict_last(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    triaged_id = await seed_alert(
        db_session, session_id="null-triaged", verdict=_verdict(severity=1), received_at=t0
    )
    pending_id = await seed_alert(
        db_session, session_id="null-pending", received_at=t0 + timedelta(minutes=2)
    )
    failed_id = await seed_alert(
        db_session,
        session_id="null-failed",
        status="failed",
        received_at=t0 + timedelta(minutes=1),
    )
    await db_session.commit()

    items, total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    assert [item.id for item in items] == [triaged_id, pending_id, failed_id]
    assert items[0].verdict is not None
    assert items[1].verdict is None
    assert items[2].verdict is None
    assert total == 3


async def test_list_alerts_uses_latest_verdict_per_alert(db_session: AsyncSession) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    newer_created_at = t0 + timedelta(hours=1)
    long_reasoning = "attacker escalated privileges and exfiltrated data; " * 4  # 208 chars
    alert_id = await seed_alert(
        db_session,
        session_id="latest-verdict",
        verdict=_verdict(severity=2),
        verdict_created_at=t0,
    )
    await add_verdict(
        db_session,
        alert_id,
        _verdict(severity=5, escalate=True, reasoning=long_reasoning),
        created_at=newer_created_at,
    )
    await db_session.commit()

    items, total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    assert total == 1
    assert len(items) == 1
    assert items[0].id == alert_id
    assert items[0].verdict is not None
    assert items[0].verdict.severity == 5
    # I2: pin the list row's VerdictSummary wiring, not just its severity — a hardcoded
    # `reasoning_excerpt=""` or `created_at=row.received_at` swap left the whole suite green.
    assert items[0].verdict.reasoning_excerpt == reasoning_excerpt(long_reasoning)
    assert len(items[0].verdict.reasoning_excerpt) == 160
    assert items[0].verdict.created_at == newer_created_at


async def test_list_alerts_pagination_tiebreaker_never_drops_or_duplicates(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    ids: set[uuid.UUID] = set()
    for i in range(7):
        alert_id = await seed_alert(
            db_session,
            session_id=f"tiebreak-{i}",
            verdict=_verdict(severity=3),
            received_at=t0,
            verdict_created_at=t0,
        )
        ids.add(alert_id)
    await db_session.commit()

    page1, total1 = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=3)
    page2, total2 = await list_alerts(db_session, filters=ListFilters(), page=2, page_size=3)
    page3, total3 = await list_alerts(db_session, filters=ListFilters(), page=3, page_size=3)

    assert [len(page1), len(page2), len(page3)] == [3, 3, 1]
    assert total1 == total2 == total3 == 7

    seen_ids = [item.id for page in (page1, page2, page3) for item in page]
    assert len(seen_ids) == len(set(seen_ids)) == 7
    assert set(seen_ids) == ids
    # Identical severity *and* received_at on every row means `id DESC` is the only remaining
    # sort key: the concatenated page order must be pure descending id, not merely a disjoint
    # partition (C1 — a stable-but-untiebroken partition could satisfy every assertion above).
    assert seen_ids == sorted(ids, reverse=True)


async def test_list_alerts_filter_severity_gte(db_session: AsyncSession) -> None:
    id3 = await seed_alert(db_session, session_id="sevgte-3", verdict=_verdict(severity=3))
    id5 = await seed_alert(
        db_session, session_id="sevgte-5", verdict=_verdict(severity=5, escalate=True)
    )
    await seed_alert(db_session, session_id="sevgte-1", verdict=_verdict(severity=1))
    await seed_alert(db_session, session_id="sevgte-pending")
    await db_session.commit()

    items, total = await list_alerts(
        db_session, filters=ListFilters(severity_gte=3), page=1, page_size=10
    )

    assert [item.id for item in items] == [id5, id3]
    assert total == 2


async def test_list_alerts_filter_category(db_session: AsyncSession) -> None:
    await seed_alert(db_session, session_id="cat-scan", verdict=_verdict(category="scanning"))
    await seed_alert(db_session, session_id="cat-brute-1", verdict=_verdict(category="brute_force"))
    await seed_alert(db_session, session_id="cat-brute-2", verdict=_verdict(category="brute_force"))
    await db_session.commit()

    items, total = await list_alerts(
        db_session, filters=ListFilters(category="brute_force"), page=1, page_size=10
    )

    assert total == 2
    assert len(items) == 2
    assert all(
        item.verdict is not None and item.verdict.category == "brute_force" for item in items
    )


async def test_list_alerts_filter_escalate(db_session: AsyncSession) -> None:
    escalated_id = await seed_alert(
        db_session, session_id="esc-true", verdict=_verdict(severity=4, escalate=True)
    )
    non_escalated_id = await seed_alert(
        db_session, session_id="esc-false", verdict=_verdict(severity=2, escalate=False)
    )
    await seed_alert(db_session, session_id="esc-pending")
    await db_session.commit()

    true_items, true_total = await list_alerts(
        db_session, filters=ListFilters(escalate=True), page=1, page_size=10
    )
    false_items, false_total = await list_alerts(
        db_session, filters=ListFilters(escalate=False), page=1, page_size=10
    )

    assert [item.id for item in true_items] == [escalated_id]
    assert true_total == 1
    assert [item.id for item in false_items] == [non_escalated_id]
    assert false_total == 1


async def test_list_alerts_filter_since_on_received_at(db_session: AsyncSession) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await seed_alert(db_session, session_id="since-older", verdict=_verdict(), received_at=t0)
    newer_id = await seed_alert(
        db_session,
        session_id="since-newer",
        verdict=_verdict(),
        received_at=t0 + timedelta(hours=1),
    )
    await db_session.commit()

    items, total = await list_alerts(
        db_session,
        filters=ListFilters(since=t0 + timedelta(minutes=30)),
        page=1,
        page_size=10,
    )

    assert [item.id for item in items] == [newer_id]
    assert total == 1


async def test_list_alerts_total_counts_filtered_rows_not_page(db_session: AsyncSession) -> None:
    for i in range(5):
        await seed_alert(db_session, session_id=f"total-{i}", verdict=_verdict(severity=3))
    await db_session.commit()

    items, total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=2)

    assert len(items) == 2
    assert total == 5


async def test_list_alerts_src_ip_and_sensor_come_from_raw(db_session: AsyncSession) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="srcip-001")
    await db_session.commit()

    items, _total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    item = next(item for item in items if item.id == alert_id)
    expected = load_alert("alert4")
    assert item.src_ip == expected.src_ip
    assert item.sensor == expected.sensor


async def test_list_alerts_projects_src_ip_and_sensor_in_sql_without_loading_raw(
    db_session: AsyncSession,
) -> None:
    """I1: `src_ip`/`sensor` must come from a `raw ->> ...` SQL projection, never from a
    Python-side `AlertRow.raw` load — asserting on the *values* alone (as the sibling test does)
    is satisfied identically by a whole-payload implementation, which is exactly what
    `core/services/alerts_read.py`'s Interfaces comment rules out."""
    await seed_alert(db_session, "alert4", session_id="sql-raw-001")
    await db_session.commit()

    statements: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    # `AsyncSession.get_bind()` proxies the underlying sync `Session.get_bind()`, which for a
    # session built by `async_sessionmaker(async_engine)` already returns the plain sync
    # `Engine` (not the `AsyncEngine`) — confirmed empirically; it has no `.sync_engine`
    # attribute of its own to unwrap further.
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements, "no SQL captured — the listener/bind wiring is broken"
    assert any("alerts.raw ->>" in stmt for stmt in statements)
    assert not any(_BARE_RAW_COLUMN.search(stmt) for stmt in statements)


async def test_get_alert_detail_returns_latest_verdict_and_tool_calls_in_seq_order(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    alert_id = await seed_alert(
        db_session,
        session_id="detail-seq",
        verdict=_verdict(severity=2),
        verdict_created_at=t0,
    )
    tool_calls = (
        ToolCallRecord(
            seq=1,
            tool_name="get_session_commands",
            arguments={"session_id": "detail-seq"},
            result={"commands": ["whoami"]},
            latency_ms=8,
        ),
        ToolCallRecord(
            seq=0,
            tool_name="check_reputation",
            arguments={"ip": "192.0.2.55"},
            result={"score": 10},
            latency_ms=12,
        ),
    )
    await add_verdict(
        db_session,
        alert_id,
        _verdict(severity=5, escalate=True),
        created_at=t0 + timedelta(hours=1),
        tool_calls=tool_calls,
    )
    await db_session.commit()

    detail = await get_alert_detail(db_session, alert_id)

    assert detail.verdict is not None
    assert detail.verdict.severity == 5
    assert [tc.seq for tc in detail.tool_calls] == [0, 1]
    assert [tc.tool_name for tc in detail.tool_calls] == [
        "check_reputation",
        "get_session_commands",
    ]


async def test_get_alert_detail_pending_alert_has_null_verdict_and_no_tool_calls(
    db_session: AsyncSession,
) -> None:
    alert_id = await seed_alert(db_session, session_id="detail-pending")
    await db_session.commit()

    detail = await get_alert_detail(db_session, alert_id)

    assert detail.verdict is None
    assert detail.tool_calls == []
    assert detail.status == "pending"


async def test_get_alert_detail_missing_raises_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await get_alert_detail(db_session, uuid.uuid4())


async def test_get_alert_detail_includes_raw_payload(db_session: AsyncSession) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="detail-raw-001")
    await db_session.commit()

    detail = await get_alert_detail(db_session, alert_id)

    expected = load_alert("alert4", session_id="detail-raw-001")
    assert detail.raw == expected.model_dump(mode="json")


async def test_get_stats_empty_db_is_zero_filled(db_session: AsyncSession) -> None:
    stats = await get_stats(db_session)

    assert stats.total_alerts == 0
    assert stats.by_status == {"pending": 0, "triaged": 0, "failed": 0}
    assert stats.by_severity == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    assert stats.by_category == {c: 0 for c in get_args(VerdictCategory)}
    assert stats.escalated_count == 0
    assert stats.volume_by_day == []
    assert stats.cost_total_usd == Decimal("0")
    assert stats.cost_mean_usd == Decimal("0.000000")
    assert stats.latency_p50_ms == 0
    assert stats.latency_p95_ms == 0
    assert stats.last_alert_at is None


async def test_get_stats_distributions_use_latest_verdict_per_alert(
    db_session: AsyncSession,
) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    multi_id = await seed_alert(
        db_session,
        session_id="stats-multi",
        verdict=_verdict(severity=2, escalate=False),
        verdict_created_at=t0,
        cost_usd=Decimal("0.000100"),
    )
    await add_verdict(
        db_session,
        multi_id,
        _verdict(severity=5, escalate=True),
        created_at=t0 + timedelta(hours=1),
        cost_usd=Decimal("0.000100"),
    )
    await seed_alert(
        db_session,
        session_id="stats-single",
        verdict=_verdict(severity=1, escalate=False),
        cost_usd=Decimal("0.000100"),
    )
    await db_session.commit()

    stats = await get_stats(db_session)

    assert stats.by_severity == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 1}
    assert stats.escalated_count == 1
    assert stats.cost_total_usd == Decimal("0.000300")


async def test_get_stats_cost_totals_and_latency_percentiles(db_session: AsyncSession) -> None:
    for latency in (10, 20, 30, 40, 100):
        await seed_alert(
            db_session,
            session_id=f"stats-lat-{latency}",
            verdict=_verdict(),
            cost_usd=Decimal("0.000100"),
            latency_ms=latency,
        )
    await seed_alert(db_session, session_id="stats-lat-pending")
    await db_session.commit()

    stats = await get_stats(db_session)

    assert stats.latency_p50_ms == 30
    assert stats.latency_p95_ms == 100
    assert stats.cost_total_usd == Decimal("0.000500")
    assert stats.cost_mean_usd == Decimal("0.000100")
    # I3: Decimal.__eq__ is numeric, so an unquantized average would still satisfy the line
    # above (e.g. "0.00010000000000000000" == Decimal("0.000100")). cost_mean_usd serializes to
    # a JSON string (same decision as VerdictOut.cost_usd), so the wire representation is the
    # thing that must be pinned.
    assert str(stats.cost_mean_usd) == "0.000100"


async def test_get_stats_volume_by_day_groups_received_at_by_utc_date(
    db_session: AsyncSession,
) -> None:
    await seed_alert(
        db_session, session_id="vol-1", received_at=datetime(2026, 9, 1, 23, 30, tzinfo=UTC)
    )
    await seed_alert(
        db_session, session_id="vol-2", received_at=datetime(2026, 9, 1, 0, 10, tzinfo=UTC)
    )
    await seed_alert(
        db_session, session_id="vol-3", received_at=datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    )
    await db_session.commit()

    stats = await get_stats(db_session)

    assert [(v.day, v.count) for v in stats.volume_by_day] == [
        (date(2026, 9, 1), 2),
        (date(2026, 9, 2), 1),
    ]


async def test_get_stats_last_alert_at_is_max_received_at(db_session: AsyncSession) -> None:
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await seed_alert(db_session, session_id="last-1", received_at=t0)
    latest = t0 + timedelta(hours=5)
    await seed_alert(db_session, session_id="last-2", received_at=latest)
    await db_session.commit()

    stats = await get_stats(db_session)

    assert stats.last_alert_at == latest
