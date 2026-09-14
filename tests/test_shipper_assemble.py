"""Pins `sentinelbrief_shipper.assemble.SessionAssembler` (m6 task-02): close-triggered payload
emission in close order, parse-error/no-connect counters, idle flush, the two truncation caps,
deterministic+signable bytes, and the rule-1 negative log pin against `_ATTACKER_STRINGS`.

Replays `fixtures/cowrie/cowrie.json` (sessions A/B/C/D, one malformed line, one
`cowrie.client.size` event — see `fixtures/cowrie/README.md`) through the real assembler; the
only fakes are an injected `clock` (idle flush) — never `httpx` here, the assembler never makes a
network call.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package, every test below fails at
collection with `ModuleNotFoundError: No module named 'sentinelbrief_shipper'`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sentinelbrief_shipper.assemble import OpenSession, SessionAssembler
from sentinelbrief_shipper.signing import sign_body as shipper_sign_body

from core.schemas.alert import SessionAlert
from core.signing import sign_body as core_sign_body

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "cowrie" / "cowrie.json"

_SESSION_A = "a1b2c3d4e5f6"
_SESSION_B = "b2c3d4e5f6a7"
_SESSION_C = "c3d4e5f6a7b8"
_SESSION_D = "d4e5f6a7b8c9"

# Rule 1 negative pin: none of these attacker-controlled substrings (from the fixture's command
# input, download URL, client banner, and a login message) may ever appear in a shipper log
# record — each is specific enough it would never occur in a legitimate shipper log line.
_ATTACKER_STRINGS = (
    "uname -a",
    "cat /etc/passwd",
    "203.0.113.9/x.sh",
    "libssh2",
    "[root/123456]",
)


def _read_fixture_lines() -> list[str]:
    return _FIXTURE_PATH.read_text().splitlines()


def _parsed_fixture_lines() -> list[tuple[str, dict[str, Any]]]:
    """Every fixture line paired with its parsed JSON, skipping the one malformed line."""
    parsed: list[tuple[str, dict[str, Any]]] = []
    for line in _read_fixture_lines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed.append((line, event))
    return parsed


def _lines_for_session(session_id: str) -> list[str]:
    return [line for line, event in _parsed_fixture_lines() if event.get("session") == session_id]


def _connect_timestamp(session_id: str) -> str:
    for _line, event in _parsed_fixture_lines():
        if event.get("session") == session_id and event.get("eventid") == "cowrie.session.connect":
            timestamp = event["timestamp"]
            assert isinstance(timestamp, str)
            return timestamp
    raise AssertionError(f"no connect event for {session_id}")


def _expected_fingerprint(session_id: str) -> str:
    """Independently recompute the PRD §6.1 fingerprint from the fixture's own connect timestamp
    — mirrors `SessionAlert.fingerprint()` without calling it, so a regression there still shows
    up here.
    """
    connect_dt = datetime.fromisoformat(_connect_timestamp(session_id).replace("Z", "+00:00"))
    connect_utc_iso = connect_dt.astimezone(UTC).isoformat()
    raw = f"cowrie|{session_id}|{connect_utc_iso}"
    return hashlib.sha256(raw.encode()).hexdigest()


_BASE_TS = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)


def _event(eventid: str, offset_s: float, *, session_id: str, **extra: Any) -> dict[str, Any]:
    timestamp = (_BASE_TS + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    event: dict[str, Any] = {
        "eventid": eventid,
        "timestamp": timestamp,
        "session": session_id,
        "src_ip": "198.51.100.30",
        "sensor": "hp-test-01",
    }
    event.update(extra)
    return event


def _open_session(session_id: str, events: list[dict[str, Any]]) -> OpenSession:
    return OpenSession(
        session_id=session_id,
        src_ip="198.51.100.30",
        sensor="hp-test-01",
        events=events,
        last_seen=0.0,
        has_connect=True,
    )


def _make_assembler(**overrides: Any) -> SessionAssembler:
    kwargs: dict[str, Any] = {
        "idle_flush_s": 900.0,
        "max_events": 2000,
        "max_payload_bytes": 1_500_000,
    }
    kwargs.update(overrides)
    return SessionAssembler(**kwargs)


class _Clock:
    """An injected, hand-advanced clock — the only fake `SessionAssembler.__init__` accepts."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_replay_yields_payloads_for_closed_sessions_in_close_order() -> None:
    """Interfaces assembler close path: feeding the full replay yields exactly B's payload then
    A's (B closes first in the fixture), never C's or D's; each payload validates as a
    `SessionAlert` and fingerprints to the value independently recomputed from the fixture's own
    connect timestamp — mutant: emitting in feed/file order regardless of which session actually
    closed first, or leaking C/D into the output.
    """
    assembler = _make_assembler()
    payloads: list[bytes] = []
    for line in _read_fixture_lines():
        payloads.extend(assembler.feed(line))

    assert len(payloads) == 2
    alerts = [SessionAlert.model_validate(json.loads(p)) for p in payloads]
    assert [a.session_id for a in alerts] == [_SESSION_B, _SESSION_A]
    assert {a.session_id for a in alerts}.isdisjoint({_SESSION_C, _SESSION_D})

    for alert in alerts:
        assert alert.fingerprint() == _expected_fingerprint(alert.session_id)


def test_malformed_line_and_no_connect_session_are_counted_not_shipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Interfaces assembler `feed`/`AssemblerStats`: the one truncated JSON line counts as a
    parse error and D (closed with no prior connect) counts as a dropped no-connect session —
    both are logged at WARNING as counters/ids only, never the raw offending text — mutant:
    silently swallowing either case, or logging the malformed line's own content.
    """
    assembler = _make_assembler()

    with caplog.at_level(logging.WARNING):
        for line in _read_fixture_lines():
            assembler.feed(line)

    assert assembler.stats.parse_errors == 1
    assert assembler.stats.dropped_no_connect == 1

    messages = [record.getMessage() for record in caplog.records]
    assert not any('{"eventid": "cowrie.' in message for message in messages)
    assert any("unparseable log line skipped" in message and "1" in message for message in messages)
    assert any(
        "session dropped, no connect event" in message and _SESSION_D in message
        for message in messages
    )


def test_idle_flush_ships_unclosed_session_after_timeout() -> None:
    """Interfaces `SessionAssembler.flush_idle`: an open, never-closed session (C) is flushed only
    once the injected clock has advanced at least `idle_flush_s` past its last event, ships
    without a `closed` event, and is forgotten afterward — mutant: flushing too early, never
    flushing, or leaving the session open after the flush.
    """
    clock = _Clock(start=1_000.0)
    assembler = _make_assembler(idle_flush_s=300.0, clock=clock)
    c_lines = _lines_for_session(_SESSION_C)
    assert len(c_lines) == 3
    for line in c_lines:
        assert assembler.feed(line) == []

    assert assembler.flush_idle() == []

    clock.now += 300.0
    flushed = assembler.flush_idle()

    assert len(flushed) == 1
    alert = SessionAlert.model_validate(json.loads(flushed[0]))
    assert alert.session_id == _SESSION_C
    assert alert.events[0].eventid == "cowrie.session.connect"
    assert not any(e.eventid == "cowrie.session.closed" for e in alert.events)
    assert assembler.open_count == 0


def test_event_cap_keeps_connect_and_closing_event() -> None:
    """Interfaces `SessionAssembler.build_payload` cap 1: a session over `max_events` keeps the
    first `max_events - 1` events plus the closing event, and reports the drop count under
    `shipper.truncated_events` — mutant: dropping the connect or the closing event, or an off-by-
    one in the kept count.
    """
    assembler = _make_assembler(max_events=5)
    session_id = "synthetic-cap1"
    events = [_event("cowrie.session.connect", 0, session_id=session_id)]
    events += [
        _event("cowrie.command.input", i, session_id=session_id, input=f"cmd-{i}")
        for i in range(1, 11)
    ]
    events.append(_event("cowrie.session.closed", 11, session_id=session_id, duration_ms=1000))
    assert len(events) == 12
    session = _open_session(session_id, events)

    payload = assembler.build_payload(session)
    envelope = json.loads(payload)

    assert len(envelope["events"]) == 5
    assert envelope["events"][0]["eventid"] == "cowrie.session.connect"
    assert envelope["events"][-1]["eventid"] == "cowrie.session.closed"
    assert envelope["shipper"]["truncated_events"] == 7


def test_byte_cap_halves_until_under_limit() -> None:
    """Interfaces `SessionAssembler.build_payload` cap 2: a session whose serialized bytes exceed
    `max_payload_bytes` is halved down until it fits (or 2 events remain), always keeping the
    connect first and the closing event last — mutant: never checking the byte cap, or dropping
    the connect/closing event while halving.
    """
    assembler = _make_assembler(max_payload_bytes=2000)
    session_id = "synthetic-cap2"
    events = [_event("cowrie.session.connect", 0, session_id=session_id)]
    events += [
        _event("cowrie.command.input", i, session_id=session_id, input="x" * 200)
        for i in range(1, 39)
    ]
    events.append(_event("cowrie.session.closed", 39, session_id=session_id, duration_ms=5000))
    assert len(events) == 40
    session = _open_session(session_id, events)

    payload = assembler.build_payload(session)
    envelope = json.loads(payload)

    assert len(payload) <= 2000
    assert len(envelope["events"]) >= 2
    assert envelope["events"][0]["eventid"] == "cowrie.session.connect"
    assert envelope["events"][-1]["eventid"] == "cowrie.session.closed"
    assert envelope["shipper"]["truncated_events"] == 40 - len(envelope["events"])


def test_payload_bytes_are_deterministic_and_signable() -> None:
    """Interfaces `SessionAssembler.build_payload`: two calls on equal sessions produce
    byte-identical output (sorted keys, fixed separators) so the vendored `sign_body` is
    reproducible over it and matches `core.signing.sign_body` — mutant: nondeterministic key
    order (e.g. plain `dict` iteration instead of `sort_keys=True`).
    """
    assembler = _make_assembler()
    session_id = "det-1"
    events = [
        _event("cowrie.session.connect", 0, session_id=session_id),
        _event("cowrie.session.closed", 5, session_id=session_id, duration_ms=5000),
    ]
    session_a = _open_session(session_id, [dict(e) for e in events])
    session_b = _open_session(session_id, [dict(e) for e in events])

    payload_a = assembler.build_payload(session_a)
    payload_b = assembler.build_payload(session_b)

    assert payload_a == payload_b

    secret = "det-secret"
    assert shipper_sign_body(secret, payload_a) == core_sign_body(secret, payload_a)


def test_no_attacker_string_in_any_log_record(caplog: pytest.LogCaptureFixture) -> None:
    """Rule 1 negative pin: after replaying the whole fixture (parse error, no-connect drop, idle
    flush) at WARNING level, no log record ever contains an attacker-controlled string — mutant:
    logging the raw offending line, a username/password, a command, or a client banner instead of
    a session id/count.
    """
    with caplog.at_level(logging.WARNING):
        assembler = _make_assembler(idle_flush_s=1.0)
        for line in _read_fixture_lines():
            assembler.feed(line)
        assembler.flush_idle()

    for record in caplog.records:
        message = record.getMessage()
        for attacker_string in _ATTACKER_STRINGS:
            assert attacker_string not in message
