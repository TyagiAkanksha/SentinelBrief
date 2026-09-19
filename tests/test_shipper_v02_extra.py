"""Implementer-added pins for the shipper v0.2 bundle (m7 task-08), beside the test-author's
`tests/test_shipper_v02.py` (pinned, untouched):

1. the two corners the new M9 batch cap creates around the EXISTING rotation and at-least-once
   behaviour, where an early close or an over-eager offset would silently lose lines the shipper
   promises never to lose (PRD §11);
2. the last two lines of the M6 final review's "t02 M5 remainder" row the bundle names but whose
   tests the Interfaces -> test table left open — the idle flush going through the spool in
   `main`, and `_session_id_for_log`'s non-JSON fallback (`main.py`, the only two uncovered
   branches left in the package).

Local helpers are sanctioned duplication (tests.md), never imports across test files. The only
fakes are `httpx.MockTransport` and an injected clock/sleep.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]` (`pyproject.toml`).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
import pytest
from sentinelbrief_shipper.assemble import SessionAssembler
from sentinelbrief_shipper.main import Backoff, drain, run_once
from sentinelbrief_shipper.post import Poster
from sentinelbrief_shipper.spool import Spool
from sentinelbrief_shipper.tail import LogTailer

_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"
_HMAC_SECRET = "test-secret"


def _poster(transport: httpx.BaseTransport) -> Poster:
    return Poster(_INGEST_URL, _HMAC_SECRET, timeout_s=5.0, transport=transport)


# --- the batch cap's corners (M9) --------------------------------------------------------------


def test_rotation_with_a_backlog_larger_than_one_batch_loses_no_line(tmp_path: Path) -> None:
    """A file rotated away while it still holds more than `max_batch_lines` unread lines is
    drained across as many calls as it takes, and only then closed — mutant: closing the rotated
    file (and clearing the buffer) on the first call that observes the new inode, which drops
    every line past the first batch plus everything still unread in it.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("".join(f"old{i}\n" for i in range(5)))

    tailer = LogTailer(log_path, state_path, read_chunk_bytes=16, max_batch_lines=2)
    assert tailer.read_new_lines() == ["old0", "old1"]

    os.rename(log_path, tmp_path / "cowrie.json.2026-09-16")
    log_path.write_text("new0\n")

    drained: list[str] = []
    for _ in range(4):
        drained.extend(tailer.read_new_lines())

    assert drained == ["old2", "old3", "old4", "new0"]
    assert json.loads(state_path.read_text())["inode"] == os.stat(log_path).st_ino


def test_uncommitted_batch_is_re_read_after_a_restart(tmp_path: Path) -> None:
    """At-least-once (M11) holds for a held-back batch too: lines returned but never committed
    are read AGAIN by a fresh tailer over the same state file, and the committed offset covers
    exactly the returned lines, never the ones still buffered — mutant: persisting the read
    position for the whole buffer, which drops the held-back lines on a crash.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("a\nb\nc\nd\n")

    first = LogTailer(log_path, state_path, max_batch_lines=2)
    assert first.read_new_lines(persist=False) == ["a", "b"]

    # Crash before the commit: a fresh tailer re-reads the same batch (the api dedups it).
    crashed = LogTailer(log_path, state_path, max_batch_lines=2)
    assert crashed.read_new_lines() == ["a", "b"]

    # That call DID commit, and its offset covers only the two lines it returned — a fresh
    # tailer picks up at `c`, never past the two lines still buffered in `crashed`.
    resumed = LogTailer(log_path, state_path, max_batch_lines=2)
    assert resumed.read_new_lines() == ["c", "d"]


def test_a_line_with_no_newline_never_grows_the_buffer_past_the_bound(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """m7 task-08 fix-1 (M4/R21) — the MEMORY half of the bound, which `tests/
    test_shipper_v02_fix1.py`'s M4 pin cannot see because its oversized line arrives complete
    (newline included) in one drain: a line still growing with no newline in sight must never
    hold more than `4 * read_chunk_bytes` in the buffer (the reviewer's probe measured an 83.9 MB
    peak for one 40 MB line). Verified by mutation: dropping the in-flight partial guard while
    keeping the split-time filter passes every other shipper test.

    `_buffer` is private and has no public accessor; the memory invariant has no other observable
    surface (same whitebox judgment as `tests/test_shipper_v02.py`'s `_sessions` and `tests/
    test_shipper_v02_fix1.py`'s `_read_chunk_bytes` pins).
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    read_chunk_bytes = 32
    bound = 4 * read_chunk_bytes
    head = "z" * 500  # no newline yet: the line is still being written

    log_path.write_text(head)
    tailer = LogTailer(
        log_path, state_path, read_chunk_bytes=read_chunk_bytes, max_batch_lines=2000
    )

    with caplog.at_level(logging.WARNING):
        assert tailer.read_new_lines() == []
    assert len(tailer._buffer) <= bound, (
        f"the buffer grew to {len(tailer._buffer)} bytes for a line with no newline — "
        f"it must never exceed 4 x read_chunk_bytes ({bound})"
    )
    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "nothing is reported until the over-long line actually ends"
    )

    tail_of_line = "z" * 100
    with log_path.open("a") as handle:
        handle.write(tail_of_line + "\ngood\n")

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert tailer.read_new_lines() == ["good"]

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected exactly one WARNING, got {warnings}"
    assert f"bytes={len(head) + len(tail_of_line) + 1}" in warnings[0].getMessage(), (
        f"the WARNING must count every byte of the dropped line: {warnings[0].getMessage()!r}"
    )


def test_oversize_line_with_nothing_after_it_still_advances_the_offset(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """m7 task-08 fix-1 (M4/R21): a call whose ONLY content was an over-long dropped line returns
    no lines — but the bytes it consumed must still be committed, or the next call (and every
    restart) re-reads and re-drops the same line forever, warning each time. `tests/
    test_shipper_v02_fix1.py`'s M4 pin has a good line after the oversized one, so the offset
    advances there through the normal path; this covers the drop-only batch — mutant: committing
    the position only when lines were returned.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    oversized = "y" * 300
    log_path.write_text(oversized + "\n")

    tailer = LogTailer(log_path, state_path, read_chunk_bytes=32, max_batch_lines=2000)
    with caplog.at_level(logging.WARNING):
        assert tailer.read_new_lines() == []
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    assert json.loads(state_path.read_text())["offset"] == len(oversized) + 1

    caplog.clear()
    resumed = LogTailer(log_path, state_path, read_chunk_bytes=32, max_batch_lines=2000)
    with caplog.at_level(logging.WARNING):
        assert resumed.read_new_lines() == []
    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "a restart re-read and re-dropped the oversized line — the offset never advanced past it"
    )


# --- t02 M5 remainder: the last two uncovered `main.py` branches --------------------------------


def test_idle_flush_payload_goes_through_the_spool_in_run_once(tmp_path: Path) -> None:
    """M5 remainder ("idle-flush -> spool in `main`"): a session Cowrie never closes is written to
    the SPOOL by the same iteration that flushes it, not POSTed straight from memory — mutant:
    dropping `flush_idle`'s payloads on the floor, which loses every session a Cowrie restart
    left open.
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text(
        json.dumps(
            {
                "eventid": "cowrie.session.connect",
                "timestamp": "2026-09-08T03:10:00.000000Z",
                "session": "idle-flush-session",
                "src_ip": "198.51.100.41",
                "sensor": "hp-test-01",
            }
        )
        + "\n"
    )
    clock = {"now": 0.0}
    tailer = LogTailer(log_path, tmp_path / "tail.json")
    assembler = SessionAssembler(
        idle_flush_s=300.0,
        max_events=2000,
        max_payload_bytes=1_500_000,
        clock=lambda: clock["now"],
    )
    spool_dir = tmp_path / "spool"
    spool = Spool(spool_dir, max_files=10)
    poster = _poster(httpx.MockTransport(lambda request: httpx.Response(500)))
    backoff = Backoff(base_s=0.01, max_s=1.0, sleep=lambda _seconds: None)

    assert run_once(tailer, assembler, spool, poster, backoff).lines_read == 1
    assert list(spool_dir.glob("*.json")) == []  # not idle yet

    clock["now"] += 300.0
    run_once(tailer, assembler, spool, poster, backoff)

    pending = sorted(spool_dir.glob("*.json"))
    assert len(pending) == 1
    assert json.loads(pending[0].read_text())["session_id"] == "idle-flush-session"

    poster.close()


def test_delivered_line_logs_unknown_for_a_payload_that_is_not_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """M5 remainder (`_session_id_for_log` fallback): a spool file that is not JSON at all (a
    truncated or corrupted file) still drains — the "delivered" line logs `session_id=unknown`
    instead of raising inside the drain loop and restarting the process — mutant: parsing the
    payload without the `JSONDecodeError` guard.
    """
    spool = Spool(tmp_path / "spool", max_files=10)
    spool.put(b"not json at all")
    backoff = Backoff(base_s=0.01, max_s=1.0, sleep=lambda _seconds: None)
    poster = _poster(httpx.MockTransport(lambda request: httpx.Response(202)))

    with caplog.at_level(logging.INFO):
        assert drain(spool, poster, backoff) == 1

    assert any("session_id=unknown" in record.getMessage() for record in caplog.records)
    assert spool.pending() == []

    poster.close()
