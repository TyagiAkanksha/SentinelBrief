"""Pins `tests/helpers.py`: the single copy of the fixture-loading, signing, row-counting and
DB-seeding helpers the four M2 test files used to each carry a private copy of (M2 final review,
plan defect 14) — m3 task-01.

Green on arrival: `tests/helpers.py` depends only on M2 code (`core.services.alerts.insert_alert`,
`worker.store.persist_verdict`), so nothing here waits on this task's new read DTOs/services.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import AlertRow, ToolCallRow, VerdictRow
from core.schemas.verdict import Verdict
from core.signing import SIGNATURE_HEADER, verify_signature
from tests.helpers import (
    FIXTURES_DIR,
    TEST_SECRET,
    add_verdict,
    count_rows,
    fixture_body,
    load_alert,
    seed_alert,
    signed_headers,
)
from worker.store import ToolCallRecord

_VERDICT = Verdict(
    severity=3,
    category="scanning",
    confidence=0.7,
    reasoning="automated scan pattern",
    recommended_action="monitor",
    escalate=False,
)


def test_fixture_body_returns_raw_file_bytes() -> None:
    assert fixture_body("alert4") == (FIXTURES_DIR / "alert4.json").read_bytes()


def test_load_alert_applies_overrides() -> None:
    base = load_alert("alert4")
    overridden = load_alert("alert4", session_id="overridden-session")

    assert overridden.session_id == "overridden-session"
    assert overridden.fingerprint() != base.fingerprint()


def test_signed_headers_verify_against_the_body() -> None:
    body = fixture_body("alert4")
    headers = signed_headers(TEST_SECRET, body)

    assert verify_signature(TEST_SECRET, body, headers[SIGNATURE_HEADER]) is True
    assert verify_signature(TEST_SECRET, b"different body", headers[SIGNATURE_HEADER]) is False


async def test_count_rows_counts_the_given_model(db_session: AsyncSession) -> None:
    await seed_alert(db_session, session_id="helpers-count-001")
    await seed_alert(db_session, session_id="helpers-count-002", verdict=_VERDICT)
    await db_session.commit()

    assert await count_rows(db_session, AlertRow) == 2
    assert await count_rows(db_session, VerdictRow) == 1


async def test_seed_alert_with_verdict_goes_through_production_writers(
    db_session: AsyncSession,
) -> None:
    tool_calls = (
        ToolCallRecord(
            seq=0,
            tool_name="check_reputation",
            arguments={"ip": "192.0.2.55"},
            result={"score": 42},
            latency_ms=12,
        ),
    )
    alert_id = await seed_alert(
        db_session, session_id="helpers-verdict-001", verdict=_VERDICT, tool_calls=tool_calls
    )
    await db_session.commit()

    row = await db_session.get(AlertRow, alert_id)
    assert row is not None
    assert row.status == "triaged"

    verdicts = (
        (await db_session.execute(select(VerdictRow).where(VerdictRow.alert_id == alert_id)))
        .scalars()
        .all()
    )
    assert len(verdicts) == 1
    assert verdicts[0].model_primary == "fake-model"

    tool_call_rows = (
        (
            await db_session.execute(
                select(ToolCallRow).where(ToolCallRow.verdict_id == verdicts[0].id)
            )
        )
        .scalars()
        .all()
    )
    assert len(tool_call_rows) == 1


async def test_seed_alert_without_verdict_is_pending(db_session: AsyncSession) -> None:
    alert_id = await seed_alert(db_session, session_id="helpers-pending-001")
    await db_session.commit()

    row = await db_session.get(AlertRow, alert_id)
    assert row is not None
    assert row.status == "pending"
    assert await count_rows(db_session, VerdictRow) == 0


async def test_seed_alert_status_override(db_session: AsyncSession) -> None:
    no_verdict_id = await seed_alert(db_session, session_id="helpers-status-001", status="failed")
    with_verdict_id = await seed_alert(
        db_session, session_id="helpers-status-002", verdict=_VERDICT, status="failed"
    )
    await db_session.commit()

    no_verdict_row = await db_session.get(AlertRow, no_verdict_id)
    with_verdict_row = await db_session.get(AlertRow, with_verdict_id)
    assert no_verdict_row is not None
    assert with_verdict_row is not None
    assert no_verdict_row.status == "failed"
    assert with_verdict_row.status == "failed"

    verdict_count = await db_session.scalar(
        select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == with_verdict_id)
    )
    assert verdict_count == 1


async def test_seed_alert_mints_a_unique_fingerprint_per_call(db_session: AsyncSession) -> None:
    first_id = await seed_alert(db_session, "alert4")
    second_id = await seed_alert(db_session, "alert4")
    await db_session.commit()

    assert first_id != second_id
    assert await count_rows(db_session, AlertRow) == 2


async def test_seed_alert_received_at_and_verdict_created_at_overrides(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    verdict_created_at = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
    second_verdict_created_at = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

    alert_id = await seed_alert(
        db_session,
        session_id="helpers-time-001",
        verdict=_VERDICT,
        received_at=received_at,
        verdict_created_at=verdict_created_at,
    )
    await db_session.commit()

    row = await db_session.get(AlertRow, alert_id)
    assert row is not None
    assert row.received_at == received_at

    verdict_row = (
        await db_session.execute(select(VerdictRow).where(VerdictRow.alert_id == alert_id))
    ).scalar_one()
    assert verdict_row.created_at == verdict_created_at

    second_verdict_id = await add_verdict(
        db_session, alert_id, _VERDICT, created_at=second_verdict_created_at
    )
    await db_session.commit()

    second_verdict_row = await db_session.get(VerdictRow, second_verdict_id)
    assert second_verdict_row is not None
    assert second_verdict_row.created_at == second_verdict_created_at
    assert await count_rows(db_session, VerdictRow) == 2
