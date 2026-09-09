"""Pins `core/services/alert_history.py::get_alert_history` (PRD §6.3, §5) — m4 task-05.

The service counts *other* sessions from the same source address in a window, through the
`ix_alerts_src_ip` expression index (`raw ->> 'src_ip'`, never a whole-`raw` load), and reports
their earliest `received_at` and their *latest*-verdict category distribution, in exactly two
statements, never committing. This file also pins the `_latest_verdicts_subquery ->
latest_verdicts_subquery` rename in `core/services/alerts_read.py` (made public so this module can
share it) as behavior-preserving.

Every seeded row goes through `tests.helpers.seed_alert`/`add_verdict`, which call the production
`insert_alert`/`persist_verdict` writers, so a fixture here is byte-for-byte what a real ingest +
triage run would produce. `T = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)` is the fixed "now" every
window is computed relative to.

Fix round 1 (m4 task-05 fix-1, review finding M1): `test_service_never_commits` now listens for
the real `ConnectionEvents.commit` event instead of a `before_cursor_execute` string check, which
SQLAlchemy's DBAPI-level COMMIT can never trip.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Subquery, event
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.alerts_read import ListFilters
from core.schemas.verdict import Verdict, VerdictCategory
from core.services import alerts_read as alerts_read_module
from core.services.alert_history import AlertHistory, get_alert_history
from core.services.alerts_read import latest_verdicts_subquery, list_alerts
from tests.helpers import add_verdict, load_alert, seed_alert

T = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

# Same pattern as `tests/test_alerts_read_service.py`'s `_BARE_RAW_COLUMN`: `alerts.raw` must
# never appear in a compiled statement except immediately followed by ` ->>` — a whole-column
# selection anywhere else would mean the service loaded a full session payload instead of
# projecting through the expression index `ix_alerts_src_ip` actually indexes.
_BARE_RAW_COLUMN = re.compile(r"alerts\.raw(?!\s*->>)")


def _verdict(*, category: VerdictCategory = "scanning") -> Verdict:
    """A `Verdict` with sensible defaults, overridable per test by category only."""
    return Verdict(
        severity=3,
        category=category,
        confidence=0.7,
        reasoning="test reasoning",
        recommended_action="monitor",
        escalate=False,
    )


async def test_latest_verdicts_subquery_is_public_and_alerts_read_still_uses_it(
    db_session: AsyncSession,
) -> None:
    assert not hasattr(alerts_read_module, "_latest_verdicts_subquery")
    assert isinstance(latest_verdicts_subquery(), Subquery)

    alert_id = await seed_alert(
        db_session,
        session_id="history-rename-001",
        verdict=_verdict(category="scanning"),
        verdict_created_at=T - timedelta(hours=2),
    )
    await add_verdict(
        db_session,
        alert_id,
        _verdict(category="brute_force"),
        created_at=T - timedelta(hours=1),
    )
    await db_session.commit()

    items, total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    assert total == 1
    assert items[0].verdict is not None
    assert items[0].verdict.category == "brute_force"


async def test_counts_alerts_from_the_ip_in_the_window_and_reports_first_seen(
    db_session: AsyncSession,
) -> None:
    src_ip = "203.0.113.10"
    since = T - timedelta(hours=24)

    # Near, in-window alert; one seeded EXACTLY at `since` (the `>=` boundary itself — a `>`
    # bound would wrongly drop it); one clearly outside the 24h window; one from a different
    # source address entirely (must never be counted regardless of its own timing).
    await seed_alert(
        db_session, session_id="hist-win-near", src_ip=src_ip, received_at=T - timedelta(hours=1)
    )
    await seed_alert(db_session, session_id="hist-win-edge", src_ip=src_ip, received_at=since)
    await seed_alert(
        db_session, session_id="hist-win-old", src_ip=src_ip, received_at=T - timedelta(hours=30)
    )
    await seed_alert(
        db_session,
        session_id="hist-win-other-ip",
        src_ip="198.51.100.23",
        received_at=T - timedelta(hours=1),
    )
    await db_session.commit()

    history = await get_alert_history(db_session, src_ip=src_ip, since=since)

    assert history.count == 2
    assert history.first_seen == since


async def test_categories_use_the_latest_verdict_per_alert_and_skip_verdictless_alerts(
    db_session: AsyncSession,
) -> None:
    src_ip = "203.0.113.10"
    since = T - timedelta(hours=24)

    alert_a = await seed_alert(
        db_session,
        session_id="hist-cat-a",
        src_ip=src_ip,
        received_at=T - timedelta(hours=1),
        verdict=_verdict(category="scanning"),
        verdict_created_at=T - timedelta(hours=2),
    )
    await add_verdict(
        db_session, alert_a, _verdict(category="brute_force"), created_at=T - timedelta(hours=1)
    )
    await seed_alert(
        db_session,
        session_id="hist-cat-b",
        src_ip=src_ip,
        received_at=T - timedelta(hours=1),
        verdict=_verdict(category="brute_force"),
    )
    await seed_alert(
        db_session, session_id="hist-cat-c", src_ip=src_ip, received_at=T - timedelta(hours=1)
    )
    await db_session.commit()

    history = await get_alert_history(db_session, src_ip=src_ip, since=since)

    assert history.count == 3
    assert history.categories == {"brute_force": 2}


async def test_exclude_fingerprint_removes_the_current_session(db_session: AsyncSession) -> None:
    src_ip = "203.0.113.10"
    session_id_1 = "hist-excl-1"

    await seed_alert(
        db_session, session_id=session_id_1, src_ip=src_ip, received_at=T - timedelta(hours=1)
    )
    await seed_alert(
        db_session, session_id="hist-excl-2", src_ip=src_ip, received_at=T - timedelta(hours=2)
    )
    await db_session.commit()

    excluded_fingerprint = load_alert(
        "alert4", session_id=session_id_1, src_ip=src_ip
    ).fingerprint()

    excluded = await get_alert_history(
        db_session,
        src_ip=src_ip,
        since=T - timedelta(hours=24),
        exclude_fingerprint=excluded_fingerprint,
    )
    included = await get_alert_history(
        db_session, src_ip=src_ip, since=T - timedelta(hours=24), exclude_fingerprint=None
    )

    assert excluded.count == 1
    assert included.count == 2


async def test_no_history_is_zero_none_empty(db_session: AsyncSession) -> None:
    history = await get_alert_history(
        db_session, src_ip="203.0.113.99", since=T - timedelta(hours=24)
    )

    assert history == AlertHistory(count=0, first_seen=None, categories={})


async def test_naive_since_raises_value_error_before_sql(db_session: AsyncSession) -> None:
    statements: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with pytest.raises(ValueError):
            await get_alert_history(db_session, src_ip="203.0.113.10", since=datetime(2026, 9, 6))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements == []


async def test_statements_use_the_src_ip_expression_and_never_select_raw(
    db_session: AsyncSession,
) -> None:
    await seed_alert(
        db_session,
        session_id="hist-sql-001",
        src_ip="203.0.113.10",
        received_at=T - timedelta(hours=1),
    )
    await db_session.commit()

    statements: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        await get_alert_history(db_session, src_ip="203.0.113.10", since=T - timedelta(hours=24))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert len(statements) == 2
    assert any("alerts.raw ->>" in stmt for stmt in statements)
    assert not any(_BARE_RAW_COLUMN.search(stmt) for stmt in statements)


async def test_service_never_commits(db_session: AsyncSession) -> None:
    """Fix round 1 (m4 task-05 fix-1, review finding M1): a `before_cursor_execute` string check
    for `"COMMIT"` can never fail — SQLAlchemy issues COMMIT through the DBAPI connection, not
    through `cursor.execute`. A `ConnectionEvents.commit` listener observes the real event
    instead; `in_transaction()` is kept as the second, independent proof."""
    await seed_alert(
        db_session,
        session_id="hist-nocommit-001",
        src_ip="203.0.113.10",
        received_at=T - timedelta(hours=1),
    )
    await db_session.flush()

    commits: list[object] = []

    def _count_commit(conn: object) -> None:
        commits.append(conn)

    engine = db_session.get_bind()
    event.listen(engine, "commit", _count_commit)
    try:
        await get_alert_history(db_session, src_ip="203.0.113.10", since=T - timedelta(hours=24))
    finally:
        event.remove(engine, "commit", _count_commit)

    assert commits == []
    assert db_session.in_transaction() is True
