"""Alert ingestion service: fingerprint-deduplicated insert, read, and status update
(PRD §6.1, §5) — session-first, `flush()`-only, never `commit()` (CONVENTIONS.md §3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFoundError
from core.models import AlertRow, AlertStatus
from core.schemas.alert import SessionAlert


@dataclass(frozen=True)
class IngestResult:
    """The outcome of `insert_alert`: the row's id, whether it was newly created, and its status."""

    alert_id: uuid.UUID
    created: bool
    status: AlertStatus


async def insert_alert(session: AsyncSession, alert: SessionAlert) -> IngestResult:
    """Insert `alert`, deduplicating on its fingerprint (PRD §6.1 step 2).

    An `ON CONFLICT (fingerprint) DO NOTHING` upsert: the first insert of a given fingerprint
    creates a `pending` row and reports `created=True`; a later insert of the same fingerprint
    inserts nothing and reports the row's *current* status — never a fresh `"pending"` — so a
    caller can tell a duplicate that already finished triage from one still in flight.

    Args:
        session: The request-scoped `AsyncSession`; never committed here.
        alert: The parsed session alert to insert.

    Returns:
        The new or existing row's id, whether it was newly created, and its current status.
    """
    fingerprint = alert.fingerprint()
    stmt = (
        pg_insert(AlertRow)
        .values(
            fingerprint=fingerprint,
            source=alert.source,
            event_time=alert.connect_time,
            raw=alert.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=["fingerprint"])
        .returning(AlertRow.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        return IngestResult(alert_id=new_id, created=True, status="pending")

    existing_id, existing_status = (
        await session.execute(
            select(AlertRow.id, AlertRow.status).where(AlertRow.fingerprint == fingerprint)
        )
    ).one()
    # `AlertRow.status` is a plain `Text` column (no DB-level enum), so the ORM only ever gives
    # mypy a `str`. The app-level invariant that it is always one of `AlertStatus`'s three
    # literals holds by construction: `insert_alert`/`set_alert_status` are the column's only
    # writers.
    return IngestResult(
        alert_id=existing_id, created=False, status=cast(AlertStatus, existing_status)
    )


async def get_alert(session: AsyncSession, alert_id: uuid.UUID) -> AlertRow:
    """Return the `AlertRow` with `alert_id`, or raise if it does not exist.

    Args:
        session: The request-scoped `AsyncSession`.
        alert_id: The alert's primary key.

    Returns:
        The matching `AlertRow`.

    Raises:
        NotFoundError: When no row with `alert_id` exists.
    """
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise NotFoundError(f"alert {alert_id} not found")
    return row


async def set_alert_status(session: AsyncSession, alert_id: uuid.UUID, status: AlertStatus) -> None:
    """Set `alert_id`'s status, flushing the change (never committing).

    Args:
        session: The request-scoped `AsyncSession`.
        alert_id: The alert's primary key.
        status: The new status.

    Raises:
        NotFoundError: When no row with `alert_id` exists.
    """
    row = await get_alert(session, alert_id)
    row.status = status
    await session.flush()
