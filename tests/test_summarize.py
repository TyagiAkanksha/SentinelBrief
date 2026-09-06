"""Pins `worker.summarize.summarize_session` / `SessionSummary` (m0 task-04).

PRD §1.2 (what the first-pass summary contains — no raw command text, attacker-controlled
strings only ever inside a bounded sample) and CONVENTIONS.md §13 (the first-pass prompt sees
`SessionSummary`, never the raw event list).

Fixture counts below were derived by hand-counting `fixtures/alerts/alert5.json`'s events
(1 `cowrie.login.failed`, 1 `cowrie.login.success`, 4 `cowrie.command.input`,
1 `cowrie.session.file_download`, 0 `cowrie.session.file_upload`) and reading the
`first_success_credential` pair straight off `alert4.json`'s events in order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.schemas.alert import SessionAlert
from worker.summarize import summarize_session

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "alerts"

_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _load_alert(name: str) -> SessionAlert:
    data = json.loads((FIXTURES_DIR / name).read_text())
    return SessionAlert.model_validate(data)


def _event(eventid: str, ts: datetime, **fields: Any) -> dict[str, Any]:
    return {
        "eventid": eventid,
        "timestamp": ts.isoformat(),
        "session": "test-session",
        "src_ip": "203.0.113.9",
        "sensor": "hp-test-01",
        **fields,
    }


def _alert(events: list[dict[str, Any]]) -> SessionAlert:
    return SessionAlert.model_validate(
        {
            "source": "cowrie",
            "session_id": "test-session",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
            "events": events,
        }
    )


def test_counts_logins_commands_downloads() -> None:
    summary = summarize_session(_load_alert("alert5.json"))

    assert summary.login_failed == 1
    assert summary.login_success == 1
    assert summary.command_count == 4
    assert summary.download_count == 1
    assert summary.upload_count == 0


def test_usernames_sample_capped_at_five_distinct() -> None:
    usernames = [f"user{i}" for i in range(1, 9)]  # 8 distinct usernames, event order
    events = [_event("cowrie.session.connect", _BASE_TS)]
    events += [
        _event("cowrie.login.failed", _BASE_TS + timedelta(seconds=i), username=name, password="x")
        for i, name in enumerate(usernames, start=1)
    ]
    events.append(
        _event("cowrie.session.closed", _BASE_TS + timedelta(seconds=9), duration_ms=1000)
    )

    summary = summarize_session(_alert(events))

    assert len(summary.usernames_sample) == 5
    assert summary.usernames_sample == usernames[:5]


def test_first_success_credential() -> None:
    summary = summarize_session(_load_alert("alert4.json"))

    assert summary.first_success_credential == ("root", "123456")


def test_first_success_credential_none_without_success() -> None:
    summary = summarize_session(_load_alert("alert2.json"))

    assert summary.first_success_credential is None


def test_duration_from_closed_event() -> None:
    alert = _load_alert("alert1.json")

    summary = summarize_session(alert)

    assert summary.duration_ms == alert.duration_ms == 1868


def test_client_version_from_client_version_event() -> None:
    summary = summarize_session(_load_alert("alert4.json"))

    assert summary.client_version == "SSH-2.0-libssh2_1.10.0"


def test_client_version_none_when_absent() -> None:
    events = [
        _event("cowrie.session.connect", _BASE_TS),
        _event("cowrie.session.closed", _BASE_TS + timedelta(seconds=1), duration_ms=1000),
    ]

    summary = summarize_session(_alert(events))

    assert summary.client_version is None
