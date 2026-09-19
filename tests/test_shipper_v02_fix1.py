"""Shipper v0.2 fix round 1 (m7 task-08 fix-1, review I2/I3/M4/M7/M8): four pins that do not bite
where they were claimed to, plus a mutation-proof interruptible-backoff pin.

Fresh file per the fix-1 brief (`tests/test_shipper_v02.py` and every m6-task-02 test-author file
stay pinned/untouched); local helpers here are sanctioned duplication (tests.md), not imports
across test files. Only fakes: `httpx.MockTransport` (never real network), a background
`threading.Thread` that sends a real `SIGTERM` (I3's "another thread"), and `caplog`.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from sentinelbrief_shipper.assemble import SessionAssembler
from sentinelbrief_shipper.config import ShipperConfig
from sentinelbrief_shipper.main import main
from sentinelbrief_shipper.tail import LogTailer

_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"
_HMAC_SECRET = "test-secret"
_REQUIRED_ENV = {"SHIPPER_INGEST_URL": _INGEST_URL, "INGEST_HMAC_SECRET": _HMAC_SECRET}

_README_PATH = Path(__file__).resolve().parent.parent / "honeypot" / "shipper" / "README.md"


def _event(eventid: str, offset_s: float, *, session_id: str, **extra: Any) -> dict[str, Any]:
    """Local copy of `tests/test_shipper_assemble.py`'s helper (tests.md: sanctioned duplication,
    never an import across test files)."""
    timestamp = f"2026-09-08T03:{10 + int(offset_s // 60):02d}:{int(offset_s % 60):02d}.000000Z"
    event: dict[str, Any] = {
        "eventid": eventid,
        "timestamp": timestamp,
        "session": session_id,
        "src_ip": "198.51.100.30",
        "sensor": "hp-test-01",
    }
    event.update(extra)
    return event


# --- I2: shipper.truncated_events counts events dropped while the session was still open -----


def test_truncated_events_counts_events_dropped_while_open() -> None:
    """Review I2 (mutant M6: `assemble.py`'s `total = len(events) + dropped_events` reverted to
    `total = len(events)`, survived all 83 mutation-suite tests): a session pushed past
    `max_events` and then closed must build a payload whose `shipper.truncated_events` equals the
    TOTAL events fed minus `max_events` — not just the events dropped by cap 1 at close (which
    would undercount, since `feed`'s incremental cap 1 already dropped some while the session was
    still open). Driven entirely through the assembler's public `feed()`, never by constructing
    `OpenSession` directly (the pinned cap-1/cap-2 tests do that, which is exactly why this was
    never exercised: `OpenSession.dropped_events` defaults to 0 there). Under the M6 mutant, a
    truncated session reports `truncated_events == 0`, `build_payload` then deletes the `shipper`
    key entirely, and a truncated payload ships claiming to be complete.
    """
    assembler = SessionAssembler(idle_flush_s=900.0, max_events=5, max_payload_bytes=1_500_000)
    session_id = "i2-truncation-check"
    n = 21  # matches the review's own probe: 21 events fed at max_events=5 -> truncated_events 16
    lines = [json.dumps(_event("cowrie.session.connect", 0, session_id=session_id))]
    lines += [
        json.dumps(_event("cowrie.command.input", i, session_id=session_id, input="x"))
        for i in range(1, n - 1)
    ]
    lines.append(
        json.dumps(_event("cowrie.session.closed", n - 1, session_id=session_id, duration_ms=1000))
    )
    assert len(lines) == n

    payloads: list[bytes] = []
    for line in lines:
        payloads.extend(assembler.feed(line))

    assert len(payloads) == 1, f"expected exactly one payload (the close), got {len(payloads)}"
    envelope = json.loads(payloads[0])

    assert envelope["shipper"]["truncated_events"] == n - 5
    assert envelope["events"][0]["eventid"] == "cowrie.session.connect"
    assert envelope["events"][-1]["eventid"] == "cowrie.session.closed"


# --- I3: a stop during backoff returns within 2s with the in-flight batch spooled -------------


def test_stop_during_backoff_returns_within_two_seconds_with_batch_spooled(tmp_path: Path) -> None:
    """Review I3: with `SHIPPER_BACKOFF_MAX_S` large enough that an uninterrupted wait would
    clearly exceed the 2-second budget below, a stop requested from ANOTHER THREAD while `drain`
    is backing off from a failed POST must return from `main`'s loop within 2 seconds, with the
    in-flight (already-spooled, not-yet-delivered) batch still on disk.

    Mutant this catches: `Backoff.wait()` sleeping on plain `time.sleep` instead of an
    interruptible wait. PEP 475 automatically RESUMES an interrupted `time.sleep` across a
    delivered signal, so `_request_stop`'s `Event.set()` alone never shortens it — the unit's
    `TimeoutStopSec=30` (`tests/test_shipper_unit_file.py`) would then be followed by a SIGKILL
    long before the SIGTERM flush this whole task exists for ever gets to run, on exactly the
    ingest-outage path the flush's guarantee ("a batch in memory at SIGTERM lands in the spool,
    not lost") is for.
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text(
        json.dumps(_event("cowrie.session.connect", 0, session_id="i3-backoff-stop"))
        + "\n"
        + json.dumps(
            _event("cowrie.session.closed", 1, session_id="i3-backoff-stop", duration_ms=1000)
        )
        + "\n"
    )
    env = {
        **_REQUIRED_ENV,
        "SHIPPER_LOG_PATH": str(log_path),
        "SHIPPER_STATE_DIR": str(tmp_path / "state"),
        "SHIPPER_BACKOFF_BASE_S": "5",
        "SHIPPER_BACKOFF_MAX_S": "5",
    }

    signalled = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not signalled["done"]:
            signalled["done"] = True
            # I3's own wording: the stop is requested from ANOTHER thread, not the one running
            # main()'s loop -- os.kill only needs to reach the process; CPython always runs the
            # registered handler on the main thread regardless of which thread signals it.
            threading.Thread(target=lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
        raise httpx.ConnectError("simulated outage", request=request)

    started = time.monotonic()
    exit_code = main(
        [],
        env=env,
        transport=httpx.MockTransport(handler),
        max_iterations=10,
    )
    elapsed = time.monotonic() - started

    assert exit_code == 0
    assert elapsed < 2.0, (
        f"main() took {elapsed:.2f}s to return after a stop requested during a backoff wait "
        f"(SHIPPER_BACKOFF_MAX_S=5) -- the wait must be interruptible, not a plain time.sleep"
    )

    spool_dir = tmp_path / "state" / "spool"
    pending = sorted(spool_dir.glob("*.json"))
    assert len(pending) == 1, f"expected the in-flight batch still in the spool, found {pending}"
    payload = json.loads(pending[0].read_text())
    assert payload["session_id"] == "i3-backoff-stop"


# --- M4: a line past 4x read_chunk_bytes is dropped with a count-only WARNING -----------------


def test_oversized_line_is_dropped_with_a_byte_count_only_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Review M4: a single line with no newline that grows past `4 * read_chunk_bytes` must be
    dropped — never buffered without bound (a 40 MB single line measured an 83.9 MB tracemalloc
    peak inside one `read_new_lines()` call today), never emitted as one giant "complete" line —
    logging exactly one WARNING that carries a byte count, never the line's own content (Cowrie
    logs attacker-supplied text into `cowrie.command.input`, PRD §10.6, and the honeypot is
    assumed compromised, PRD §10.4). The FOLLOWING line must still be read intact.
    """
    log_path = tmp_path / "cowrie.json"
    read_chunk_bytes = 64
    bound = 4 * read_chunk_bytes
    oversized = "x" * (bound + 5)
    log_path.write_text(oversized + "\nsecond-line\n")

    tailer = LogTailer(
        log_path, tmp_path / "tail.json", read_chunk_bytes=read_chunk_bytes, max_batch_lines=2000
    )

    collected: list[str] = []
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            batch = tailer.read_new_lines()
            collected.extend(batch)
            if collected:
                break

    assert "second-line" in collected, (
        f"the line after the oversized one was never read intact: {collected}"
    )
    assert all(len(line) < bound for line in collected), (
        "the oversized line must never be returned as one complete line: "
        f"{[len(line) for line in collected]}"
    )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected exactly one WARNING, got {len(warnings)}: {warnings}"
    message = warnings[0].getMessage()
    assert "x" * 20 not in message, f"the WARNING must never carry the line's content: {message!r}"
    assert str(bound) in message or any(c.isdigit() for c in message), (
        f"the WARNING must carry a byte count: {message!r}"
    )


# --- M7: the README tunables rows exist with the documented defaults --------------------------


def test_readme_tunables_rows_exist_with_documented_defaults() -> None:
    """Review M7: the two new tunables (`SHIPPER_READ_CHUNK_BYTES`, `SHIPPER_MAX_BATCH_LINES`)
    must have a README row naming the SAME default `ShipperConfig` carries, so the table cannot
    silently drift from the code. Already true today (GREEN's commit added both rows to
    `honeypot/shipper/README.md`) — pinned here as a durable regression guard, not a RED-driving
    test; see the test-author report's "real split" for why that is correct, not a mistake.
    """
    readme = _README_PATH.read_text()
    default_cfg = ShipperConfig.from_env(_REQUIRED_ENV)

    assert "SHIPPER_READ_CHUNK_BYTES" in readme, (
        f"{_README_PATH} has no SHIPPER_READ_CHUNK_BYTES row"
    )
    assert str(default_cfg.read_chunk_bytes) in readme, (
        f"{_README_PATH} does not name the default {default_cfg.read_chunk_bytes}"
    )
    assert "SHIPPER_MAX_BATCH_LINES" in readme, f"{_README_PATH} has no SHIPPER_MAX_BATCH_LINES row"
    assert str(default_cfg.max_batch_lines) in readme, (
        f"{_README_PATH} does not name the default {default_cfg.max_batch_lines}"
    )


# --- M8: LogTailer's fallback defaults equal ShipperConfig's (one source of truth in practice) -


def test_log_tailer_defaults_equal_shipper_config_defaults(tmp_path: Path) -> None:
    """Review M8: `tail.py`'s fallback defaults (restated so the pinned 2-arg
    `LogTailer(path, state_path)` constructions keep working without `ShipperConfig` in scope)
    must equal `ShipperConfig`'s own defaults — the two literals can otherwise drift silently.
    Mutant: bumping one literal in `tail.py` without the matching change in `config.py`.
    """
    default_cfg = ShipperConfig.from_env(_REQUIRED_ENV)
    tailer = LogTailer(tmp_path / "cowrie.json", tmp_path / "tail.json")

    assert tailer._read_chunk_bytes == default_cfg.read_chunk_bytes, (
        f"LogTailer's default read_chunk_bytes ({tailer._read_chunk_bytes}) != "
        f"ShipperConfig's ({default_cfg.read_chunk_bytes})"
    )
    assert tailer._max_batch_lines == default_cfg.max_batch_lines, (
        f"LogTailer's default max_batch_lines ({tailer._max_batch_lines}) != "
        f"ShipperConfig's ({default_cfg.max_batch_lines})"
    )
