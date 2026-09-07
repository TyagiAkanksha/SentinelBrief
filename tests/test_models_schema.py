"""Schema tests for the PRD §5 tables: defaults, constraints, and the four §5 indexes.

All tests here use the `db_session`/`db_engine`/`tmp_schema` fixtures (CONVENTIONS.md §10) —
they run for real against a throwaway Postgres schema when `TEST_DATABASE_URL` is set, and are
skipped by fixture name otherwise (see `tests/conftest.py::pytest_collection_modifyitems`), which
`test_db_fixtures_skip_without_test_database_url` below pins directly (m2 task-01).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from core.models import AlertRow, VerdictRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytest_plugins = ["pytester"]

_RAW_ALERT: dict[str, Any] = {
    "source": "cowrie",
    "session_id": "s1",
    "src_ip": "203.0.113.7",
    "sensor": "sensor1",
    "events": [],
}


def _alert_kwargs(*, fingerprint: str) -> dict[str, Any]:
    """Minimal valid `AlertRow` kwargs: fingerprint/source/event_time/raw only (PRD §5)."""
    return {
        "fingerprint": fingerprint,
        "source": "cowrie",
        "event_time": datetime(2026, 1, 1, tzinfo=UTC),
        "raw": _RAW_ALERT,
    }


def _verdict_kwargs(*, alert_id: uuid.UUID, severity: int) -> dict[str, Any]:
    """Minimal valid `VerdictRow` kwargs against `alert_id`, with a given `severity` (PRD §5)."""
    return {
        "alert_id": alert_id,
        "severity": severity,
        "category": "scanning",
        "confidence": 0.5,
        "reasoning": "automated scan pattern",
        "recommended_action": "monitor",
        "escalate": False,
        "model_primary": "fake-model",
        "model_final": "fake-model",
        "escalated_model": False,
        "prompt_version": "triage-v1",
    }


async def test_alert_insert_sets_id_received_at_pending(db_session: AsyncSession) -> None:
    """Inserting an `AlertRow` with only fingerprint/source/event_time/raw yields the server
    defaults: a UUID id, a non-null `received_at`, and `status == "pending"` (PRD §5)."""
    alert = AlertRow(**_alert_kwargs(fingerprint="fp-defaults"))
    db_session.add(alert)
    await db_session.flush()

    assert isinstance(alert.id, uuid.UUID)
    assert alert.received_at is not None
    assert alert.status == "pending"


async def test_duplicate_fingerprint_raises_integrity_error(db_session: AsyncSession) -> None:
    """`alerts.fingerprint` is UNIQUE (PRD §5) — the ingest dedup key never admits a duplicate."""
    db_session.add(AlertRow(**_alert_kwargs(fingerprint="dup-fp")))
    await db_session.flush()

    db_session.add(AlertRow(**_alert_kwargs(fingerprint="dup-fp")))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_verdict_severity_check_rejects_out_of_range(db_session: AsyncSession) -> None:
    """`verdicts.severity` is CHECKed to 1..5 (PRD §5) — 6 must violate `ck_verdicts_severity`."""
    alert = AlertRow(**_alert_kwargs(fingerprint="fp-severity"))
    db_session.add(alert)
    await db_session.flush()

    verdict = VerdictRow(**_verdict_kwargs(alert_id=alert.id, severity=6))
    db_session.add(verdict)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_verdict_requires_existing_alert(db_session: AsyncSession) -> None:
    """`verdicts.alert_id` FKs to `alerts.id` (PRD §5) — a nonexistent alert id must be rejected."""
    verdict = VerdictRow(**_verdict_kwargs(alert_id=uuid.uuid4(), severity=3))
    db_session.add(verdict)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_migration_creates_all_four_tables(
    db_engine: AsyncEngine, tmp_schema: tuple[str, str]
) -> None:
    """`alembic upgrade head` creates exactly the four PRD §5 tables in the test schema."""
    _, schema = tmp_schema

    async with db_engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: set(sa.inspect(sync_conn).get_table_names(schema=schema))
        )

    # Alembic's own `alembic_version` bookkeeping table legitimately lives in this schema too
    # (`version_table_schema=schema`, per the brief) — exclude it rather than assert its absence.
    assert table_names - {"alembic_version"} == {"alerts", "verdicts", "tool_calls", "eval_runs"}


async def test_indexes_present(db_engine: AsyncEngine, tmp_schema: tuple[str, str]) -> None:
    """The four named §5 indexes exist after migration: `ix_alerts_received_at`,
    `ix_alerts_src_ip`, `ix_verdicts_alert_id_created_at`, `ix_tool_calls_verdict_id_seq`."""
    _, schema = tmp_schema

    def _index_names(sync_conn: sa.Connection, table_name: str) -> set[str]:
        return {ix["name"] for ix in sa.inspect(sync_conn).get_indexes(table_name, schema=schema)}

    async with db_engine.connect() as conn:
        alerts_indexes = await conn.run_sync(_index_names, "alerts")
        verdicts_indexes = await conn.run_sync(_index_names, "verdicts")
        tool_calls_indexes = await conn.run_sync(_index_names, "tool_calls")

    assert "ix_alerts_received_at" in alerts_indexes
    assert "ix_alerts_src_ip" in alerts_indexes
    assert "ix_verdicts_alert_id_created_at" in verdicts_indexes
    assert "ix_tool_calls_verdict_id_seq" in tool_calls_indexes


def test_db_fixtures_skip_without_test_database_url(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the collection-time skip-by-fixture-name hook (CONVENTIONS.md §10): with
    `TEST_DATABASE_URL` unset, a test requesting `db_session` is reported `skipped` — never
    `passed` and never silently omitted, so a green suite without the export line can never look
    like it exercised DB coverage. Copies the *real* `tests/conftest.py` source into an isolated
    pytester project so this exercises the actual hook, not a re-implementation of it.
    """
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    real_conftest_source = (Path(__file__).resolve().parent / "conftest.py").read_text()
    pytester.makeconftest(real_conftest_source)
    pytester.makepyfile(
        test_needs_db_session="""
        def test_needs_db_session(db_session):
            assert db_session is not None
        """
    )

    result = pytester.runpytest()

    result.assert_outcomes(passed=0, skipped=1, failed=0, errors=0)
