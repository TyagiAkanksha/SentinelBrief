"""Pins core.schemas.alert.SessionAlert: fixtures, ordering, fingerprint (m0 task-02).

PRD §1.2 (raw shape), §6.1 step 2 (fingerprint formula), §6.6 (fixtures must earn their band —
covered by the implementer's fixture authoring, not asserted here beyond schema validity).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.schemas.alert import SessionAlert

SESSION_ID = "a1b2c3d4e5f6"
SRC_IP = "203.0.113.42"
SENSOR = "hp-sgp-01"

FIXTURE_DIR = Path("fixtures/alerts")
FIXTURE_PATHS = sorted(FIXTURE_DIR.glob("alert*.json"))


def _event(eventid: str, ts: str, **extra: object) -> dict[str, object]:
    return {
        "eventid": eventid,
        "timestamp": ts,
        "session": SESSION_ID,
        "src_ip": SRC_IP,
        "sensor": SENSOR,
        "message": f"{eventid} event",
        **extra,
    }


def _alert(events: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "source": "cowrie",
        "session_id": SESSION_ID,
        "src_ip": SRC_IP,
        "sensor": SENSOR,
        "events": events,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "fixture_path",
    FIXTURE_PATHS or [FIXTURE_DIR / "MISSING.json"],
    ids=lambda p: p.name,
)
def test_all_five_fixtures_validate(fixture_path: Path) -> None:
    assert len(FIXTURE_PATHS) == 5, (
        f"expected exactly 5 fixture files under {FIXTURE_DIR}/, found {len(FIXTURE_PATHS)}"
    )

    data = json.loads(fixture_path.read_text())
    alert = SessionAlert.model_validate(data)

    assert alert.events
    assert alert.events[0].eventid == "cowrie.session.connect"


def test_events_must_start_with_session_connect() -> None:
    events = [
        _event(
            "cowrie.login.failed",
            "2026-09-06T14:03:20.000000Z",
            username="root",
            password="root",
        ),
        _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
    ]

    with pytest.raises(ValidationError):
        SessionAlert.model_validate(_alert(events))


def test_events_sorted_by_timestamp() -> None:
    connect = _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z")
    login = _event(
        "cowrie.login.failed",
        "2026-09-06T14:03:22.115004Z",
        username="root",
        password="root",
    )
    closed = _event(
        "cowrie.session.closed",
        "2026-09-06T14:03:31.104450Z",
        duration_ms=9623,
    )
    shuffled = [closed, connect, login]

    alert = SessionAlert.model_validate(_alert(shuffled))

    assert [e.eventid for e in alert.events] == [
        "cowrie.session.connect",
        "cowrie.login.failed",
        "cowrie.session.closed",
    ]


def test_fingerprint_is_sha256_of_source_session_connect_time() -> None:
    events = [
        _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
        _event("cowrie.session.closed", "2026-09-06T14:03:31.104450Z", duration_ms=9623),
    ]
    alert = SessionAlert.model_validate(_alert(events))

    connect_utc_iso = datetime(2026, 9, 6, 14, 3, 21, 481902, tzinfo=UTC).isoformat()
    expected = hashlib.sha256(
        "|".join(["cowrie", SESSION_ID, connect_utc_iso]).encode()
    ).hexdigest()

    fingerprint = alert.fingerprint()
    assert fingerprint == expected
    assert len(fingerprint) == 64


def test_fingerprint_stable_across_timezone_spellings() -> None:
    spellings = [
        "2026-09-06T14:03:21.481902Z",
        "2026-09-06T14:03:21.481902+00:00",
        "2026-09-06T16:03:21.481902+02:00",
    ]

    fingerprints = set()
    for ts in spellings:
        events = [
            _event("cowrie.session.connect", ts),
            _event("cowrie.session.closed", "2026-09-06T14:03:31.104450Z", duration_ms=9623),
        ]
        alert = SessionAlert.model_validate(_alert(events))
        fingerprints.add(alert.fingerprint())

    connect_utc_iso = datetime(2026, 9, 6, 14, 3, 21, 481902, tzinfo=UTC).isoformat()
    expected = hashlib.sha256(
        "|".join(["cowrie", SESSION_ID, connect_utc_iso]).encode()
    ).hexdigest()

    assert fingerprints == {expected}


def test_duration_ms_from_closed_event() -> None:
    events = [
        _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
        _event("cowrie.session.closed", "2026-09-06T14:03:31.104450Z", duration_ms=9623),
    ]
    alert = SessionAlert.model_validate(_alert(events))

    assert alert.duration_ms == 9623
    assert alert.close_time is not None


def test_duration_ms_falls_back_to_close_minus_connect() -> None:
    events = [
        _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
        _event("cowrie.session.closed", "2026-09-06T14:03:31.103902Z"),
    ]
    alert = SessionAlert.model_validate(_alert(events))

    assert alert.duration_ms == 9622
    assert alert.close_time is not None


def test_duration_ms_none_when_never_closed() -> None:
    events = [
        _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
        _event(
            "cowrie.login.failed",
            "2026-09-06T14:03:22.115004Z",
            username="root",
            password="root",
        ),
    ]
    alert = SessionAlert.model_validate(_alert(events))

    assert alert.duration_ms is None
    assert alert.close_time is None


def test_extra_cowrie_fields_survive_model_dump() -> None:
    hassh_value = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    events = [
        _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
        _event(
            "cowrie.client.kex",
            "2026-09-06T14:03:21.702118Z",
            hassh=hassh_value,
            hasshAlgorithms="curve25519-sha256,ecdh-sha2-nistp256",
        ),
        _event("cowrie.session.closed", "2026-09-06T14:03:31.104450Z", duration_ms=9623),
    ]
    alert = SessionAlert.model_validate(_alert(events))

    dumped = alert.model_dump()
    kex_event = next(e for e in dumped["events"] if e["eventid"] == "cowrie.client.kex")

    assert kex_event["hassh"] == hassh_value
