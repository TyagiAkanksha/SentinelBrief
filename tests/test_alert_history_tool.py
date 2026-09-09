"""Pins `worker/tools/alert_history.py::AlertHistoryTool` (PRD §6.3) — m4 task-05.

`AlertHistoryTool` is the only tool that touches the database: it validates and clamps its
arguments, excludes the session being triaged by fingerprint, and runs `get_alert_history` inside
a `session.begin_nested()` SAVEPOINT so a database error can never poison the triage transaction
`persist_verdict` needs afterwards (PRD §6.2). Without a session (the CLI, evals) it answers
`unavailable("no_database")` before ever touching SQL.

`T = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)` is the fixed "now" `ToolContext.now` carries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from tests.helpers import load_alert, seed_alert
from worker.tools import ReplayToolRecorder, ToolContext, fixture_key, unavailable
from worker.tools.alert_history import AlertHistoryTool

T = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


async def test_returns_history_for_the_context_ip_excluding_the_current_session(
    db_session: AsyncSession,
) -> None:
    context_session_id = "tool-ctx-001"
    context_alert = load_alert("alert4", session_id=context_session_id)

    await seed_alert(db_session, "alert4", session_id=context_session_id, received_at=T)
    await seed_alert(
        db_session,
        "alert4",
        session_id="tool-ctx-earlier-1",
        src_ip="192.0.2.55",
        received_at=T - timedelta(hours=2),
    )
    await seed_alert(
        db_session,
        "alert4",
        session_id="tool-ctx-earlier-2",
        src_ip="192.0.2.55",
        received_at=T - timedelta(hours=5),
    )
    await db_session.commit()

    ctx = ToolContext(alert=context_alert, session=db_session, now=T)
    tool = AlertHistoryTool(max_window_hours=720)

    result = await tool.run({"ip": "192.0.2.55", "window_hours": 24}, ctx)

    assert result["ip"] == "192.0.2.55"
    assert result["window_hours"] == 24
    # Would be 3 if the context session's own alert were not excluded by fingerprint.
    assert result["count"] == 2
    assert isinstance(result["first_seen"], str)
    assert result["first_seen"].endswith("+00:00")
    assert isinstance(result["categories"], dict)


async def test_window_hours_above_max_is_clamped_and_reported(db_session: AsyncSession) -> None:
    context_session_id = "tool-clamp-ctx"
    context_alert = load_alert("alert4", session_id=context_session_id)

    await seed_alert(db_session, "alert4", session_id=context_session_id, received_at=T)
    await seed_alert(
        db_session,
        "alert4",
        session_id="tool-clamp-old",
        src_ip="192.0.2.55",
        received_at=T - timedelta(hours=60),
    )
    await db_session.commit()

    ctx = ToolContext(alert=context_alert, session=db_session, now=T)
    tool = AlertHistoryTool(max_window_hours=48)

    result = await tool.run({"ip": "192.0.2.55", "window_hours": 5000}, ctx)

    assert result["window_hours"] == 48
    # Would be 1 if the raw (unclamped) window_hours=5000 were used instead of 48.
    assert result["count"] == 0


async def test_invalid_ip_or_window_is_invalid_arguments() -> None:
    alert = load_alert("alert4", session_id="tool-invalid-ctx")
    ctx = ToolContext(alert=alert, session=None, now=T)
    tool = AlertHistoryTool(max_window_hours=720)

    cases: list[dict[str, object]] = [
        {"ip": "192.0.2.55"},  # window_hours missing
        {"ip": "192.0.2.55", "window_hours": 0},
        {"ip": "192.0.2.55", "window_hours": "24"},
        {"ip": "192.0.2.55", "window_hours": True},  # bool is explicitly excluded, not an int
        {"ip": "x", "window_hours": 24},
    ]
    for arguments in cases:
        result = await tool.run(arguments, ctx)
        assert result == unavailable("invalid_arguments"), arguments


async def test_no_session_is_unavailable_no_database() -> None:
    alert = load_alert("alert4", session_id="tool-nodb-ctx")
    ctx = ToolContext(alert=alert, session=None, now=T)
    tool = AlertHistoryTool(max_window_hours=720)

    result = await tool.run({"ip": "192.0.2.55", "window_hours": 24}, ctx)

    assert result == unavailable("no_database")


async def test_database_error_is_unavailable_and_the_transaction_survives(
    db_session: AsyncSession,
) -> None:
    context_session_id = "tool-dberr-ctx"
    context_alert = load_alert("alert4", session_id=context_session_id)

    await seed_alert(db_session, "alert4", session_id=context_session_id, received_at=T)
    await db_session.commit()

    await db_session.execute(text("DROP TABLE verdicts CASCADE"))

    ctx = ToolContext(alert=context_alert, session=db_session, now=T)
    tool = AlertHistoryTool(max_window_hours=720)

    result = await tool.run({"ip": "192.0.2.55", "window_hours": 24}, ctx)

    assert result == unavailable("database_error")

    # The SAVEPOINT rolled back on failure — the outer transaction must still be usable.
    check = await db_session.execute(text("SELECT 1"))
    assert check.scalar() == 1


def test_rejects_nonpositive_max_window() -> None:
    with pytest.raises(ValueError):
        AlertHistoryTool(max_window_hours=0)


def test_tool_is_external() -> None:
    tool = AlertHistoryTool(max_window_hours=720)

    assert tool.external is True


def test_max_window_setting_default_and_bound() -> None:
    assert Settings().alert_history_max_window_hours == 720

    with pytest.raises(ValidationError):
        Settings(alert_history_max_window_hours=0)


async def test_recorded_fixtures_replay_for_the_five_fixture_ips() -> None:
    alert = load_alert("alert4", session_id="tool-fixtures-ctx")
    ctx = ToolContext(alert=alert, session=None, now=T)
    tool = AlertHistoryTool(max_window_hours=720)
    recorder = ReplayToolRecorder(Path("tests/fixtures/tools"))

    expected_counts = {
        "203.0.113.10": 3,
        "198.51.100.23": 12,
        "203.0.113.77": 0,
        "192.0.2.55": 1,
        "198.51.100.140": 2,
    }

    for ip, expected_count in expected_counts.items():
        arguments = {"ip": ip, "window_hours": 24}
        result = await recorder.execute(tool, arguments, ctx)
        assert result["count"] == expected_count, ip

    fixture_dir = Path("tests/fixtures/tools") / tool.name
    filenames = {path.name for path in fixture_dir.iterdir()}
    expected_filenames = {
        f"{fixture_key({'ip': ip, 'window_hours': 24})}.json" for ip in expected_counts
    }
    assert filenames == expected_filenames
