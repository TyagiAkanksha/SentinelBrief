"""Pins `worker.tools.session_commands.SessionCommandsTool`: `get_session_commands` returns the
commands the attacker typed (`cowrie.command.input`) and the files they downloaded
(`cowrie.session.file_download`) from the `SessionAlert` already in `ToolContext` — no separate
session store, no DB round-trip (PRD §6.3) — capped at the configured maxima with total counts
always reported, each command clipped to a character budget (m4 task-02).

PRD §6.3 ("max 40 commands from a session, summarized count beyond that"); `worker/summarize.py`
counts `cowrie.command.input` *and* `cowrie.command.failed` toward the session's command tally,
but Cowrie logs a `command.failed` *in addition* to `command.input` for every unrecognized line
(nothing typed is lost) — this tool returns `command.input` only, so a `command.failed` sharing
an already-typed command's text must never be double-counted here.

Synthetic events are built in-file from an `_alert()`/`_event()` pair (the shape used by
`tests/test_triage_pipeline.py::_minimal_alert` and `tests/test_tool_registry.py::_make_alert`);
documentation-range IPs only (CONVENTIONS.md §10), sensor hostnames from the real fleet list.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.config import Settings
from core.schemas.alert import SessionAlert
from tests.helpers import fixture_body, load_alert
from worker.tools import ToolContext, truncate_result, unavailable
from worker.tools.session_commands import SessionCommandsTool

_SENSOR = "hp-eu-01"
_SRC_IP = "203.0.113.10"
_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _event(
    eventid: str,
    *,
    session_id: str,
    sensor: str = _SENSOR,
    src_ip: str = _SRC_IP,
    when: datetime,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "eventid": eventid,
        "timestamp": when.isoformat(),
        "session": session_id,
        "src_ip": src_ip,
        "sensor": sensor,
        **extra,
    }


def _alert(session_id: str, events: list[dict[str, Any]]) -> SessionAlert:
    return SessionAlert.model_validate(
        {
            "source": "cowrie",
            "session_id": session_id,
            "src_ip": _SRC_IP,
            "sensor": _SENSOR,
            "events": events,
        }
    )


async def test_returns_commands_in_event_order_for_the_alert_in_context() -> None:
    alert = load_alert("alert4")
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": alert.session_id}, ctx)

    assert result["commands"] == ["uname -a", "cat /etc/passwd", "w"]
    assert result["command_count"] == 3
    assert result["commands_truncated"] is False
    assert result["downloads"] == []

    # A `cowrie.command.failed` event sharing an already-typed command's input must not be
    # double-counted (PRD §6.3: this tool returns `command.input` only).
    raw = json.loads(fixture_body("alert4"))
    raw["events"].append(
        _event(
            "cowrie.command.failed",
            session_id=alert.session_id,
            sensor=alert.sensor,
            src_ip=alert.src_ip,
            when=datetime(2026, 9, 6, 12, 20, 9, 500000, tzinfo=UTC),
            input="uname -a",
        )
    )
    alert_with_failed = SessionAlert.model_validate(raw)
    ctx_with_failed = ToolContext(alert=alert_with_failed, session=None, now=_BASE_TS)

    result_with_failed = await tool.run({"session_id": alert.session_id}, ctx_with_failed)

    assert result_with_failed["commands"] == ["uname -a", "cat /etc/passwd", "w"]
    assert result_with_failed["command_count"] == 3


async def test_returns_downloads_with_url_outfile_shasum() -> None:
    alert = load_alert("alert5")
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": alert.session_id}, ctx)

    assert result["downloads"] == [
        {
            "url": "http://203.0.113.200/x.sh",
            "outfile": "/var/lib/cowrie/downloads/x.sh",
            "shasum": "d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f7081920a3b4c5d6e7f8091a2b3c4d5e6",
        }
    ]
    assert result["download_count"] == 1


async def test_caps_commands_at_max_and_reports_total_count() -> None:
    session_id = "cap-commands"
    commands = [f"cmd{i:04d}".ljust(60, "-") for i in range(45)]
    events = [_event("cowrie.session.connect", session_id=session_id, when=_BASE_TS)]
    events += [
        _event(
            "cowrie.command.input",
            session_id=session_id,
            when=_BASE_TS + timedelta(seconds=i + 1),
            input=cmd,
        )
        for i, cmd in enumerate(commands)
    ]
    alert = _alert(session_id, events)
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": session_id}, ctx)

    assert result["commands"] == commands[:40]
    assert result["command_count"] == 45
    assert result["commands_truncated"] is True


async def test_caps_downloads_at_max() -> None:
    session_id = "cap-downloads"
    downloads_meta = [
        {
            "url": f"http://203.0.113.{50 + i}/x{i}.sh",
            "outfile": f"/var/lib/cowrie/downloads/x{i}.sh",
            "shasum": f"{'a' * 63}{i}",
        }
        for i in range(3)
    ]
    events = [_event("cowrie.session.connect", session_id=session_id, when=_BASE_TS)]
    events += [
        _event(
            "cowrie.session.file_download",
            session_id=session_id,
            when=_BASE_TS + timedelta(seconds=i + 1),
            **meta,
        )
        for i, meta in enumerate(downloads_meta)
    ]
    alert = _alert(session_id, events)
    tool = SessionCommandsTool(max_commands=40, max_downloads=2, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": session_id}, ctx)

    assert result["downloads"] == downloads_meta[:2]
    assert result["download_count"] == 3
    assert result["downloads_truncated"] is True


async def test_clips_each_command_to_max_command_chars() -> None:
    session_id = "clip-commands"
    long_command = "x" * 500
    boundary_command = "y" * 200
    events = [
        _event("cowrie.session.connect", session_id=session_id, when=_BASE_TS),
        _event(
            "cowrie.command.input",
            session_id=session_id,
            when=_BASE_TS + timedelta(seconds=1),
            input=long_command,
        ),
        _event(
            "cowrie.command.input",
            session_id=session_id,
            when=_BASE_TS + timedelta(seconds=2),
            input=boundary_command,
        ),
    ]
    alert = _alert(session_id, events)
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": session_id}, ctx)

    assert result["commands"][0] == long_command[:200]
    assert len(result["commands"][0]) == 200
    assert result["commands"][1] == boundary_command
    assert result["clipped_commands"] == 1


async def test_clipped_commands_counts_the_whole_session_not_the_kept_slice() -> None:
    """I3 (controller ruling, brief amendment 3f1dcb2): `clipped_commands` counts every
    over-length command in the whole session, not just the ones that land in the kept
    (`max_commands`-sized) slice. 45 commands, `max_commands=40`; only commands 41-45 (outside
    the kept window) exceed `max_command_chars` — a "kept slice only" implementation would report
    `clipped_commands == 0` here, since none of the first 40 need clipping.
    """
    session_id = "clip-outside-window"
    short_commands = [f"short{i:03d}" for i in range(40)]
    long_commands = [f"long{i:03d}".ljust(250, "-") for i in range(40, 45)]
    commands = short_commands + long_commands
    events = [_event("cowrie.session.connect", session_id=session_id, when=_BASE_TS)]
    events += [
        _event(
            "cowrie.command.input",
            session_id=session_id,
            when=_BASE_TS + timedelta(seconds=i + 1),
            input=cmd,
        )
        for i, cmd in enumerate(commands)
    ]
    alert = _alert(session_id, events)
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": session_id}, ctx)

    assert result["commands"] == short_commands
    assert len(result["commands"]) == 40
    assert result["clipped_commands"] == 5


async def test_unknown_session_id_is_unavailable() -> None:
    alert = load_alert("alert4")
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": "not-this-session"}, ctx)

    assert result == unavailable("unknown_session")


async def test_missing_or_non_string_session_id_is_invalid_arguments() -> None:
    alert = load_alert("alert4")
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    assert await tool.run({}, ctx) == unavailable("invalid_arguments")
    assert await tool.run({"session_id": 7}, ctx) == unavailable("invalid_arguments")


def test_rejects_nonpositive_caps() -> None:
    with pytest.raises(ValueError):
        SessionCommandsTool(max_commands=0, max_downloads=10, max_command_chars=200)
    with pytest.raises(ValueError):
        SessionCommandsTool(max_commands=40, max_downloads=0, max_command_chars=200)
    with pytest.raises(ValueError):
        SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=0)


async def test_forty_default_length_commands_fit_the_default_result_budget() -> None:
    # Reads the defaults from `Settings()` rather than the literals 4000/40/200 (controller
    # ruling R6): proves the structural caps and the result-size backstop agree, so a future
    # change to either default without the other is caught here.
    settings = Settings()
    session_id = "default-budget"
    commands = [f"cmd{i:04d}".ljust(60, "-") for i in range(settings.tool_session_commands_max)]
    events = [_event("cowrie.session.connect", session_id=session_id, when=_BASE_TS)]
    events += [
        _event(
            "cowrie.command.input",
            session_id=session_id,
            when=_BASE_TS + timedelta(seconds=i + 1),
            input=cmd,
        )
        for i, cmd in enumerate(commands)
    ]
    alert = _alert(session_id, events)
    tool = SessionCommandsTool(
        max_commands=settings.tool_session_commands_max,
        max_downloads=settings.tool_session_downloads_max,
        max_command_chars=settings.tool_command_max_chars,
    )
    ctx = ToolContext(alert=alert, session=None, now=_BASE_TS)

    result = await tool.run({"session_id": session_id}, ctx)

    assert truncate_result(result, settings.tool_result_max_chars) == result
