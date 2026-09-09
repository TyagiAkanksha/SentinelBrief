"""`get_alert_history`: read-only session history over `ix_alerts_src_ip` (PRD §5, §6.3).

Counts *other* sessions from the same source address received in a window, through the
expression index `ix_alerts_src_ip` (`raw ->> 'src_ip'`, never a whole-`raw` load), and reports
their earliest `received_at` and the distribution of their *latest*-verdict categories (the same
`DISTINCT ON` subquery `core.services.alerts_read` uses, `latest_verdicts_subquery`, made public
for this module — m4 task-05). Session-first, read-only: two statements, never `flush()`, never
`commit()` (CONVENTIONS.md §3) — the history read must never poison the triage transaction
`persist_verdict` needs afterwards (PRD §6.2); `worker.tools.alert_history.AlertHistoryTool` is
what isolates it further under a SAVEPOINT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import AlertRow
from core.services.alerts_read import latest_verdicts_subquery


@dataclass(frozen=True)
class AlertHistory:
    """The `get_alert_history` result: count, earliest sighting, latest-verdict category counts."""

    count: int
    """Alerts from `src_ip` with `received_at >= since` (minus the excluded fingerprint, if any);
    includes verdict-less (pending/failed) alerts."""
    first_seen: datetime | None
    """`min(received_at)` over those alerts; `None` when `count == 0`."""
    categories: dict[str, int]
    """Latest-verdict category -> count, over those alerts; only categories actually present (no
    zero-fill); a pending/failed alert counts toward `count` but contributes no category key."""


async def get_alert_history(
    session: AsyncSession,
    *,
    src_ip: str,
    since: datetime,
    exclude_fingerprint: str | None = None,
) -> AlertHistory:
    """Count other sessions from `src_ip` received at or after `since` (PRD §6.3).

    Args:
        session: The request/triage-scoped `AsyncSession`; never flushed or committed here.
        src_ip: The source address to match, compared by equality only — never interpolated.
            The caller (`AlertHistoryTool`) is responsible for validating it as an IP address.
        since: The window's inclusive lower bound; must be timezone-aware.
        exclude_fingerprint: When given, the alert with this fingerprint (typically the session
            being triaged) is excluded from every count — never itself its own history.

    Returns:
        `AlertHistory(count, first_seen, categories)`.

    Raises:
        ValueError: When `since` is a naive datetime — raised before any SQL is issued.
    """
    if since.tzinfo is None or since.tzinfo.utcoffset(since) is None:
        raise ValueError("since must be timezone-aware")

    predicates = [AlertRow.raw["src_ip"].astext == src_ip, AlertRow.received_at >= since]
    if exclude_fingerprint is not None:
        predicates.append(AlertRow.fingerprint != exclude_fingerprint)

    count_stmt = select(func.count(), func.min(AlertRow.received_at)).where(*predicates)
    count, first_seen = (await session.execute(count_stmt)).one()

    latest = latest_verdicts_subquery()
    category_stmt = (
        select(latest.c.category, func.count())
        .select_from(AlertRow)
        .join(latest, latest.c.alert_id == AlertRow.id)
        .where(*predicates)
        .group_by(latest.c.category)
    )
    category_rows = (await session.execute(category_stmt)).all()
    categories = {category: category_count for category, category_count in category_rows}

    return AlertHistory(count=count, first_seen=first_seen, categories=categories)
