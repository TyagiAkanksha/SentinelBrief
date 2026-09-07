"""Pins `core/services/alerts.py`: insert-with-fingerprint-dedup (PRD §6.1, §5) — m2 task-03.

`insert_alert` is an `ON CONFLICT (fingerprint) DO NOTHING` upsert: the first insert of a session
creates a `pending` row; every later insert of the *same* session must report `created=False` and
the row's *current* status — never a fresh "pending" — so a caller can tell a genuine retry from
one that already finished triage. `get_alert`/`set_alert_status` back the read and status-write
paths the route and any future retriage need.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFoundError
from core.models import AlertRow
from core.schemas.alert import SessionAlert
from core.services.alerts import get_alert, insert_alert, set_alert_status

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "alerts" / "alert4.json"


def _load_alert(**overrides: object) -> SessionAlert:
    """Load `fixtures/alerts/alert4.json`, applying top-level field overrides.

    A distinct `session_id` per test mints a fresh fingerprint without hand-rolling a whole
    session payload (`SessionAlert.fingerprint()` is `sha256(source|session_id|connect_time)`).
    """
    data = json.loads(_FIXTURE.read_text())
    data.update(overrides)
    return SessionAlert.model_validate(data)


async def _count_alerts(session: AsyncSession) -> int:
    count = await session.scalar(select(func.count()).select_from(AlertRow))
    assert count is not None
    return count


async def test_insert_alert_new_returns_created_true_pending(db_session: AsyncSession) -> None:
    alert = _load_alert(session_id="new-session-001")

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
    alert = _load_alert(session_id="dup-session-002")

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

    assert await _count_alerts(db_session) == 1


async def test_get_alert_missing_raises_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await get_alert(db_session, uuid.uuid4())


async def test_set_alert_status(db_session: AsyncSession) -> None:
    alert = _load_alert(session_id="status-session-003")
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
