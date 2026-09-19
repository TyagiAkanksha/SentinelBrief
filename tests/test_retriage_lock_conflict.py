"""Pins `core.services.alerts.get_alert_for_retriage_update`'s lock-contention path (m8b task-04,
the m5 task-04 deferral "the retriage flip needs `SET LOCAL lock_timeout` + 409").

`tests/test_admin_retriage.py`'s own module docstring documents this path as skipped there
("hard to drive deterministically without racing two real transactions... the task brief marks it
optional/hard for the test-author"); this file adds it at the service layer instead, mirroring
`tests/test_job_idempotency.py::test_overlapping_attempts_serialise_on_the_row_lock_and_the_loser_skips`'s
two-real-session pattern (rule 9: `db_engine`'s pool has >= 2 connections, so session A's held
lock and session B's blocked `SELECT ... FOR UPDATE` can be open at once without B's own
connection checkout being what blocks it).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.errors import ConflictError
from core.models import AlertRow
from core.services.alerts import get_alert_for_retriage_update
from tests.helpers import seed_alert


async def test_lock_held_by_another_transaction_raises_conflict_before_the_deadline(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert_id = await seed_alert(db_session, "alert4", status="triaged", session_id="lock-conf-001")
    await db_session.commit()

    async with db_session_factory() as session_a, db_session_factory() as session_b:
        # Session A takes and HOLDS the row lock (no `lock_timeout` needed here; it never waits
        # on anything) and stays open — never committed/rolled back — until the `async with`
        # block exits, well after session B's attempt below.
        await session_a.execute(select(AlertRow).where(AlertRow.id == alert_id).with_for_update())

        with pytest.raises(ConflictError):
            await asyncio.wait_for(
                get_alert_for_retriage_update(session_b, alert_id, lock_timeout_ms=200), timeout=5
            )
