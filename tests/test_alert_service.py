"""Pins `core/services/alerts.py`: insert-with-fingerprint-dedup (PRD §6.1, §5) — m2 task-03.

`insert_alert` is an `ON CONFLICT (fingerprint) DO NOTHING` upsert: the first insert of a session
creates a `pending` row; every later insert of the *same* session must report `created=False` and
the row's *current* status — never a fresh "pending" — so a caller can tell a genuine retry from
one that already finished triage. `get_alert`/`set_alert_status` back the read and status-write
paths the route and any future retriage need.

m5 task-04 (PRD §6.1 idempotency, §12 M5) adds `get_alert_for_update`: the blocking `SELECT ...
FOR UPDATE` `worker.triage.TriagePipeline.triage_attempt` holds for the whole attempt, so a
concurrent duplicate run waits for the truth (this transaction's commit or rollback) instead of
racing it, per the M5 spine's "row lock, not SKIP LOCKED" ruling.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.errors import NotFoundError
from core.models import AlertRow
from core.services.alerts import get_alert, get_alert_for_update, insert_alert, set_alert_status
from tests.helpers import count_rows, load_alert


async def test_insert_alert_new_returns_created_true_pending(db_session: AsyncSession) -> None:
    alert = load_alert(session_id="new-session-001")

    result = await insert_alert(db_session, alert)
    await db_session.commit()

    assert isinstance(result.alert_id, uuid.UUID)
    assert result.created is True
    assert result.status == "pending"

    row = await db_session.get(AlertRow, result.alert_id)
    assert row is not None
    assert row.raw == alert.model_dump(mode="json")
    assert row.event_time == alert.connect_time


async def test_insert_conflict_returns_existing_id_and_status(db_session: AsyncSession) -> None:
    alert = load_alert(session_id="dup-session-002")

    first = await insert_alert(db_session, alert)
    await db_session.commit()
    assert first.created is True

    second = await insert_alert(db_session, alert)
    await db_session.commit()
    assert second.created is False
    assert second.alert_id == first.alert_id
    assert second.status == "pending"

    await set_alert_status(db_session, first.alert_id, "triaged")
    await db_session.commit()

    third = await insert_alert(db_session, alert)
    await db_session.commit()
    assert third.created is False
    assert third.alert_id == first.alert_id
    assert third.status == "triaged"

    assert await count_rows(db_session, AlertRow) == 1


async def test_get_alert_missing_raises_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await get_alert(db_session, uuid.uuid4())


async def test_set_alert_status(db_session: AsyncSession) -> None:
    alert = load_alert(session_id="status-session-003")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    await set_alert_status(db_session, result.alert_id, "triaged")
    await db_session.commit()
    row = await get_alert(db_session, result.alert_id)
    assert row.status == "triaged"

    await set_alert_status(db_session, result.alert_id, "failed")
    await db_session.commit()
    row = await get_alert(db_session, result.alert_id)
    assert row.status == "failed"


async def test_get_alert_for_update_returns_the_row_or_raises(db_session: AsyncSession) -> None:
    alert = load_alert(session_id="lock-returns-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    row = await get_alert_for_update(db_session, result.alert_id)
    assert row.id == result.alert_id

    with pytest.raises(NotFoundError):
        await get_alert_for_update(db_session, uuid.uuid4())


async def test_get_alert_for_update_blocks_until_the_holder_commits(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Assumes `db_engine`'s pool has >= 2 connections (SQLAlchemy's default is 5, M4 plan-defect
    rule 9): session A's held lock and session B's blocked `SELECT ... FOR UPDATE` must run over
    two DISTINCT pooled connections at once, or B's own connection checkout would hang first and
    this test would prove pool exhaustion, not the row lock (m5 task-04, PRD §6.1 idempotency)."""
    alert = load_alert(session_id="lock-blocks-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    await get_alert_for_update(db_session, result.alert_id)  # session A now holds the row lock

    async def _lock_from_b() -> AlertRow:
        async with db_session_factory() as session_b:
            row = await get_alert_for_update(session_b, result.alert_id)
            await session_b.commit()
            return row

    task = asyncio.create_task(_lock_from_b())
    try:
        await asyncio.sleep(0.3)
        assert not task.done()  # still blocked behind A's uncommitted lock

        await db_session.commit()  # releases A's lock

        row = await asyncio.wait_for(task, timeout=2.0)
        assert row.id == result.alert_id
    finally:
        if not task.done():
            task.cancel()
