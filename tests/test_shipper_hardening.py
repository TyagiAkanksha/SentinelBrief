"""Hardening pins added in fix round 1 (m6 task-02, review findings I1, I2, M1, M2, M5, M7):
an unreadable log file must never crash the poll loop (I1); the spool's atomic write must be
pinned, not merely satisfied incidentally (I2); the `shipper` envelope key's own bytes must count
toward the byte cap (M1); `SHIPPER_MAX_EVENTS` must be floored at 2 so cap 1 can never drop the
connect event (M2); an idle no-connect session must be dropped, not shipped (M5a); non-numeric and
zero numeric overrides must raise without leaking the value or the secret (M5b); a `state_dir`
collaborator-construction failure must exit 1 without a traceback (M7).

Fresh file per the fix-1 brief (the eight test-author files stay pinned/untouched); local helpers
here are sanctioned duplication (tests.md), not imports across test files.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sentinelbrief_shipper.assemble import OpenSession, SessionAssembler
from sentinelbrief_shipper.config import ShipperConfig
from sentinelbrief_shipper.main import main
from sentinelbrief_shipper.spool import Spool
from sentinelbrief_shipper.tail import LogTailer

_BASE_TS = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)


def _event(eventid: str, offset_s: float, *, session_id: str, **extra: Any) -> dict[str, Any]:
    """Local copy of `tests/test_shipper_assemble.py`'s helper (tests.md: sanctioned duplication,
    never an import across test files)."""
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


_REQUIRED_ENV = {
    "SHIPPER_INGEST_URL": "https://ingest.example.invalid/api/v1/alerts",
    "INGEST_HMAC_SECRET": "s3cr3t-required-value",
}


# --- I1: an unreadable log file never crashes the poll loop ----------------------------------


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores file permissions; chmod 000 would not deny reads"
)
def test_unreadable_log_file_returns_empty_and_rate_limits_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`LogTailer.read_new_lines` never raises on a permission-denied log file — it returns `[]`
    and logs the WARNING once per distinct errno, never again until a successful read.
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text("a\nb\n")
    log_path.chmod(0o000)
    tailer = LogTailer(log_path, tmp_path / "tail.json")

    with caplog.at_level(logging.WARNING):
        assert tailer.read_new_lines() == []
        assert tailer.read_new_lines() == []
        assert tailer.read_new_lines() == []

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "log file unreadable errno=" in warnings[0].getMessage()

    log_path.chmod(0o644)
    assert tailer.read_new_lines() == ["a", "b"]


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores directory permissions; chmod 000 would not deny reads",
)
def test_unsearchable_parent_directory_is_a_warning_not_a_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """N1 (review re-review 1): the log's PARENT DIRECTORY being unsearchable — not just the
    file itself — must never crash `read_new_lines` either. `Path.exists()`/`Path.open()` both
    raise `PermissionError` (not merely returning a falsy/missing result) when the containing
    directory denies search (`x`) permission, so the check has to live INSIDE the same
    rate-limited-WARNING handling I1 added, never ahead of it.
    """
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    log_path = log_dir / "cowrie.json"
    log_path.write_text("a\nb\n")
    log_dir.chmod(0o000)
    tailer = LogTailer(log_path, tmp_path / "tail.json")

    try:
        with caplog.at_level(logging.WARNING):
            assert tailer.read_new_lines() == []

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "log file unreadable errno=" in warnings[0].getMessage()
    finally:
        log_dir.chmod(0o755)

    assert tailer.read_new_lines() == ["a", "b"]


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores directory permissions; chmod 000 would not deny reads",
)
def test_main_once_exits_zero_with_unsearchable_log_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """N1: the same unsearchable-parent-directory case must not escape `main` either — `--once`
    exits 0 (nothing to tail this iteration), never an uncaught traceback (the reviewer's own
    reproduction showed the exception escaping `main` before this fix).
    """
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    log_path = log_dir / "cowrie.json"
    log_path.write_text("a\nb\n")
    log_dir.chmod(0o000)
    env = {
        **_REQUIRED_ENV,
        "SHIPPER_LOG_PATH": str(log_path),
        "SHIPPER_STATE_DIR": str(tmp_path / "state"),
    }

    try:
        exit_code = main(["--once"], env=env)
    finally:
        log_dir.chmod(0o755)

    assert exit_code == 0
    assert "Traceback" not in capsys.readouterr().err


# --- I2: the spool's atomic write is pinned ------------------------------------------------


def test_put_cleans_up_tmp_and_propagates_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `Spool.put` interrupted right after the tmp write (an `os.replace` failure) propagates
    the error, leaves NOTHING in `pending()`, and cleans up its own `.tmp` file — a subsequent
    `put` then succeeds normally.
    """
    spool = Spool(tmp_path / "spool", max_files=10)

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated replace failure")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", flaky_replace)

    with pytest.raises(OSError):
        spool.put(b'{"n":1}')

    assert spool.pending() == []
    assert list((tmp_path / "spool").glob("*.tmp")) == []

    second = spool.put(b'{"n":2}')
    assert spool.pending() == [second]


# --- M1: the shipper envelope key's bytes count toward the byte cap ------------------------


def test_byte_cap_final_bytes_respect_the_cap_including_the_shipper_key() -> None:
    """At a tight `max_payload_bytes`, the FINAL dumped bytes (with the `shipper` key already
    included) are at or under the cap, or the 2-event floor was hit — never silently over cap
    because the key's own bytes were measured without it (M1).
    """
    assembler = SessionAssembler(idle_flush_s=900.0, max_events=2000, max_payload_bytes=400)

    session_id = "synthetic-m1"
    events = [_event("cowrie.session.connect", 0, session_id=session_id)]
    events += [
        _event("cowrie.command.input", i, session_id=session_id, input="x" * 200)
        for i in range(1, 4)
    ]
    events.append(_event("cowrie.session.closed", 4, session_id=session_id, duration_ms=1000))
    session = OpenSession(
        session_id=session_id,
        src_ip="198.51.100.30",
        sensor="hp-test-01",
        events=events,
        last_seen=0.0,
        has_connect=True,
    )

    payload = assembler.build_payload(session)
    envelope = json.loads(payload)

    assert len(payload) <= 400 or len(envelope["events"]) == 2


# --- M2: SHIPPER_MAX_EVENTS is floored at 2 --------------------------------------------------


def test_max_events_of_one_is_rejected_at_config_time() -> None:
    """`SHIPPER_MAX_EVENTS=1` would make cap 1 drop the connect event
    (`events[:0] + [events[-1]]`), producing a payload the api always rejects — refused at
    `from_env` instead, naming the variable.
    """
    with pytest.raises(ValueError) as exc:
        ShipperConfig.from_env({**_REQUIRED_ENV, "SHIPPER_MAX_EVENTS": "1"})
    assert "SHIPPER_MAX_EVENTS" in str(exc.value)


def test_max_events_of_two_is_accepted() -> None:
    """The floor is inclusive: `2` (the minimum a `SessionAlert` can ever validate with) works."""
    cfg = ShipperConfig.from_env({**_REQUIRED_ENV, "SHIPPER_MAX_EVENTS": "2"})
    assert cfg.max_events == 2


# --- M5(a): an idle no-connect session is dropped, not shipped -----------------------------


def test_flush_idle_drops_no_connect_session_without_shipping_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`flush_idle` on a session with no connect event (Cowrie started mid-session) drops it with
    the same WARNING `feed`'s close path uses, and returns no payload for it.
    """
    clock = {"now": 0.0}
    assembler = SessionAssembler(
        idle_flush_s=300.0, max_events=2000, max_payload_bytes=1_500_000, clock=lambda: clock["now"]
    )
    no_connect_line = (
        '{"eventid":"cowrie.command.input","timestamp":"2026-09-08T03:10:00.000000Z",'
        '"session":"mid-session-only","src_ip":"198.51.100.99","sensor":"hp-test-01",'
        '"input":"ls -la"}'
    )
    assert assembler.feed(no_connect_line) == []

    clock["now"] += 300.0
    with caplog.at_level(logging.WARNING):
        flushed = assembler.flush_idle()

    assert flushed == []
    assert assembler.open_count == 0
    assert assembler.stats.dropped_no_connect == 1
    assert any(
        "session dropped, no connect event" in r.getMessage()
        and "mid-session-only" in r.getMessage()
        for r in caplog.records
    )


# --- M5(b): non-numeric and zero numeric overrides raise without leaking anything ----------


def test_non_numeric_idle_flush_raises_naming_the_var_only() -> None:
    canary_secret = "canary-secret-do-not-leak"
    env = {
        "SHIPPER_INGEST_URL": _REQUIRED_ENV["SHIPPER_INGEST_URL"],
        "INGEST_HMAC_SECRET": canary_secret,
        "SHIPPER_IDLE_FLUSH_S": "abc",
    }
    with pytest.raises(ValueError) as exc:
        ShipperConfig.from_env(env)
    message = str(exc.value)
    assert "SHIPPER_IDLE_FLUSH_S" in message
    assert "abc" not in message
    assert canary_secret not in message


def test_zero_post_timeout_raises_naming_the_var_only() -> None:
    canary_secret = "canary-secret-do-not-leak-2"
    env = {
        "SHIPPER_INGEST_URL": _REQUIRED_ENV["SHIPPER_INGEST_URL"],
        "INGEST_HMAC_SECRET": canary_secret,
        "SHIPPER_POST_TIMEOUT_S": "0",
    }
    with pytest.raises(ValueError) as exc:
        ShipperConfig.from_env(env)
    message = str(exc.value)
    assert "SHIPPER_POST_TIMEOUT_S" in message
    assert "0" not in message
    assert canary_secret not in message


# --- M7: a state_dir collaborator-construction failure exits 1, no traceback ---------------


def test_state_dir_is_a_regular_file_exits_1_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`SHIPPER_STATE_DIR` pointing at a regular FILE fails collaborator construction; `main`
    exits 1 with exactly one stderr line, never an uncaught traceback (M7).
    """
    state_dir_as_file = tmp_path / "state"
    state_dir_as_file.write_text("not a directory")
    env = {**_REQUIRED_ENV, "SHIPPER_STATE_DIR": str(state_dir_as_file)}

    exit_code = main(["--once"], env=env)

    assert exit_code == 1
    captured = capsys.readouterr()
    stderr_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(stderr_lines) == 1
    assert "Traceback" not in captured.err
