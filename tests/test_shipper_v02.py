"""Shipper v0.2 hardening bundle (m7 task-08 — M6 final review §5 DEFER-TO-M7 rows: t02 M5
remainder, M9, M11, M12, new M5, new M8; the fix shapes there are the contract, see this file's
per-test docstrings for the exact row).

Fresh file per the task-08 brief (the eight m6-task-02 test-author files and their fix-round-1
siblings stay pinned/untouched); local helpers here are sanctioned duplication (tests.md), not
imports across test files. Only fakes: `httpx.MockTransport` (never real network), an injected
`sleep`, `caplog`/`capsys`, and — for the chunked-read pin — a `Path.open` spy that wraps the real
file so `.read(size)` calls can be observed without touching `LogTailer`'s private state.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]` (`pyproject.toml`).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, BinaryIO

import httpx
import pytest
from sentinelbrief_shipper.assemble import SessionAssembler
from sentinelbrief_shipper.config import ShipperConfig
from sentinelbrief_shipper.main import Backoff, main, run_once
from sentinelbrief_shipper.post import Poster
from sentinelbrief_shipper.spool import Spool
from sentinelbrief_shipper.tail import LogTailer

_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"
_HMAC_SECRET = "test-secret"
_REQUIRED_ENV = {"SHIPPER_INGEST_URL": _INGEST_URL, "INGEST_HMAC_SECRET": _HMAC_SECRET}

_ONE_CLOSED_SESSION = (
    '{"eventid":"cowrie.session.connect","timestamp":"2026-09-08T03:10:00.000000Z",'
    '"session":"ordering-check","src_ip":"198.51.100.40","sensor":"hp-test-01"}\n'
    '{"eventid":"cowrie.session.closed","timestamp":"2026-09-08T03:10:05.000000Z",'
    '"session":"ordering-check","src_ip":"198.51.100.40","sensor":"hp-test-01","duration_ms":5000}\n'
)


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


# --- SIGTERM handler: flushes the in-flight batch to the spool (M5 remainder, main.py:236/256) --


def test_sigterm_flushes_in_flight_batch_to_spool(tmp_path: Path) -> None:
    """M6 final review row t02 (M5 remainder): the real SIGTERM handler path (`main.py`'s
    `_request_stop` / `stop_requested.is_set()`) is never exercised by the existing `stop_event`-
    only SIGTERM-simulation test, and today a session the assembler is still accumulating (open,
    not closed, not yet idle) is silently lost when the process exits — `Restart=always` starts
    fresh with an empty assembler. A REAL SIGTERM must force that in-flight batch into the spool
    before `main` returns. The transport always fails, so the only way the payload can be
    observed is that it actually landed on disk (never delivered, never lost either).
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text(
        json.dumps(_event("cowrie.session.connect", 0, session_id="sigterm-inflight")) + "\n"
    )
    env = {
        **_REQUIRED_ENV,
        "SHIPPER_LOG_PATH": str(log_path),
        "SHIPPER_STATE_DIR": str(tmp_path / "state"),
        "SHIPPER_IDLE_FLUSH_S": "99999",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated outage", request=request)

    fired = {"done": False}

    def sleep_then_sigterm(_seconds: float) -> None:
        if not fired["done"]:
            fired["done"] = True
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.1)  # give the interpreter a chance to deliver the pending signal

    exit_code = main(
        [],
        env=env,
        transport=httpx.MockTransport(handler),
        max_iterations=20,
        sleep=sleep_then_sigterm,
    )

    assert exit_code == 0
    spool_dir = tmp_path / "state" / "spool"
    pending = sorted(spool_dir.glob("*.json"))
    assert len(pending) == 1, (
        f"expected the still-open session flushed to the spool on SIGTERM, found {len(pending)} "
        f"pending file(s): {pending}"
    )
    payload = json.loads(pending[0].read_text())
    assert payload["session_id"] == "sigterm-inflight"
    assert any(e["eventid"] == "cowrie.session.connect" for e in payload["events"])


# --- at-least-once: Spool.write then Tailer.commit_offset (M11) ------------------------------


def test_offset_commits_after_spool_write(tmp_path: Path) -> None:
    """M11: `run_once`'s new at-least-once ordering is `Spool.write` THEN `Tailer.commit_offset`
    (Interfaces block) — a crash between them re-ships the payload on restart (the api dedups by
    fingerprint, so a replay is a 200, never a re-triage) instead of the OLD order's silent drop
    (offset already advanced past a closed session whose spool write never happened). Pinned by
    recording call order on the real collaborators, not by description.
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text(_ONE_CLOSED_SESSION)
    tailer = LogTailer(log_path, tmp_path / "tail.json")
    assembler = SessionAssembler(idle_flush_s=900.0, max_events=2000, max_payload_bytes=1_500_000)
    spool = Spool(tmp_path / "spool", max_files=10)
    poster = Poster(
        _INGEST_URL,
        _HMAC_SECRET,
        timeout_s=5.0,
        transport=httpx.MockTransport(lambda request: httpx.Response(202)),
    )
    backoff = Backoff(base_s=0.01, max_s=1.0, sleep=lambda _seconds: None)

    calls: list[str] = []
    real_put = spool.put

    def recording_put(payload: bytes) -> Path:
        calls.append("spool.put")
        return real_put(payload)

    spool.put = recording_put  # type: ignore[method-assign]

    real_commit = tailer.commit_offset  # AttributeError today: commit_offset does not exist yet

    def recording_commit() -> None:
        calls.append("tailer.commit_offset")
        real_commit()

    tailer.commit_offset = recording_commit  # type: ignore[method-assign]

    run_once(tailer, assembler, spool, poster, backoff)

    assert "spool.put" in calls, f"spool.put was never called: {calls}"
    assert "tailer.commit_offset" in calls, f"tailer.commit_offset was never called: {calls}"
    assert calls.index("spool.put") < calls.index("tailer.commit_offset"), (
        f"expected spool.put before tailer.commit_offset, got: {calls}"
    )

    poster.close()


# --- cold start: bounded chunk reads + a per-call batch cap (M9) -----------------------------


class _ChunkRecorder:
    """Wraps a real binary file, recording every `.read(size)` call's requested size — the only
    way to observe `LogTailer`'s internal read-loop bound from outside.
    """

    def __init__(self, real_file: BinaryIO) -> None:
        self._real = real_file
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._real.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._real.seek(offset, whence)

    def tell(self) -> int:
        return self._real.tell()

    def close(self) -> None:
        self._real.close()


def test_cold_start_reads_in_bounded_chunks_and_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M9: a cold start (a large backlog already on disk, e.g. after a restart) reads the log in
    BOUNDED chunks — never one unbounded `.read()` call for the whole file — and a single
    `read_new_lines()` call stops at `max_batch_lines`, leaving the rest for the NEXT call.
    Interfaces: `LogTailer` gains two new keyword-only tunables, `read_chunk_bytes` and
    `max_batch_lines`, mirroring `ShipperConfig`'s new `SHIPPER_READ_CHUNK_BYTES` (default 8 MiB)
    / `SHIPPER_MAX_BATCH_LINES` (default 2000) — both parsed here directly off `ShipperConfig`.
    """
    # -- bounded chunk size: a long line forces multiple `.read(read_chunk_bytes)` calls -------
    log_path = tmp_path / "cowrie.json"
    long_line = ("x" * 200) + "\n"
    log_path.write_text(long_line * 3)

    real_open = Path.open
    recorders: list[_ChunkRecorder] = []

    def spy_open(path_self: Path, *args: object, **kwargs: object) -> object:
        real_file = real_open(path_self, *args, **kwargs)  # type: ignore[arg-type]
        if path_self == log_path:
            recorder = _ChunkRecorder(real_file)  # type: ignore[arg-type]
            recorders.append(recorder)
            return recorder
        return real_file

    monkeypatch.setattr(Path, "open", spy_open)

    tailer = LogTailer(log_path, tmp_path / "tail.json", read_chunk_bytes=64, max_batch_lines=2000)
    lines = tailer.read_new_lines()

    assert lines == [long_line.rstrip("\n")] * 3
    assert recorders, "LogTailer never opened the log through Path.open"
    sizes = recorders[0].read_sizes
    assert sizes, "read_new_lines() never called .read() on the open file"
    assert all(size <= 64 for size in sizes), (
        f"a chunked cold start must never request more than read_chunk_bytes (64) per "
        f".read() call: {sizes}"
    )
    assert any(size == 64 for size in sizes), (
        f"expected at least one full-size (64-byte) chunk request for a 600+-byte file: {sizes}"
    )
    assert len(sizes) > 1, "a 600+-byte file at a 64-byte chunk size must take more than one read"

    # -- batch cap: a single call never returns more than max_batch_lines lines ----------------
    batch_log_path = tmp_path / "batch.json"
    batch_log_path.write_text("".join(f"line{i}\n" for i in range(5)))
    batch_tailer = LogTailer(
        batch_log_path,
        tmp_path / "batch-tail.json",
        read_chunk_bytes=8 * 1024 * 1024,
        max_batch_lines=3,
    )

    first = batch_tailer.read_new_lines()
    assert first == ["line0", "line1", "line2"]
    second = batch_tailer.read_new_lines()
    assert second == ["line3", "line4"]

    # -- ShipperConfig: the two new tunables parse with the documented defaults and override ---
    default_cfg = ShipperConfig.from_env(_REQUIRED_ENV)
    assert default_cfg.read_chunk_bytes == 8 * 1024 * 1024
    assert default_cfg.max_batch_lines == 2000

    overridden_cfg = ShipperConfig.from_env(
        {**_REQUIRED_ENV, "SHIPPER_READ_CHUNK_BYTES": "1024", "SHIPPER_MAX_BATCH_LINES": "10"}
    )
    assert overridden_cfg.read_chunk_bytes == 1024
    assert overridden_cfg.max_batch_lines == 10


# --- unreadable state dir: a clean startup error, distinct root cause from the pinned file ----


def test_unreadable_state_dir_is_a_clean_startup_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M6 final review row t02 (M5 remainder): an EXISTING but unwritable state directory — as
    opposed to `tests/test_shipper_hardening.py`'s pinned "state dir is a regular file" case —
    fails the SAME collaborator-construction guard for a DIFFERENT reason: `Spool`'s own `mkdir`
    for its `spool/` subdirectory needs write+search permission on the parent it cannot get.
    `main` must still exit 1 with one stderr line, never a traceback.

    Runs unconditionally (fix-1 ruling, review M6: the prior `skipif(os.geteuid() == 0, ...)`
    made the "0 skipped" gate vacuous for this assertion on a root CI runner) — this repo's own
    runs are never root, so behavior here is unchanged; a future root runner now gets a real
    result (pass or a loud failure) instead of a silent skip.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_dir.chmod(0o000)
    env = {**_REQUIRED_ENV, "SHIPPER_STATE_DIR": str(state_dir)}

    try:
        exit_code = main(["--once"], env=env)
    finally:
        state_dir.chmod(0o755)

    assert exit_code == 1
    captured = capsys.readouterr()
    stderr_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(stderr_lines) == 1
    assert "state dir unreadable" in captured.err
    assert "Traceback" not in captured.err


# --- corrupt tail.json / vanished-while-open: both survivable ---------------------------------


def test_corrupt_tail_json_and_vanished_log_are_survivable(tmp_path: Path) -> None:
    """M6 final review row t02 (M5 remainder, `tail.py:69-70` vanished-while-open / `tail.py:
    125-126` corrupt `tail.json`): neither condition may crash `read_new_lines` — both continue
    from a sane state (a correct read from the current file, never a stuck or wrong offset).
    """
    # -- corrupt tail.json: `_load_state`'s broad except must recover, not crash --------------
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("a\nb\n")
    state_path.write_text("{not valid json")

    tailer = LogTailer(log_path, state_path)
    assert tailer.read_new_lines() == ["a", "b"]

    # -- vanished-while-open: `os.stat` raising OSError on the held-open file's path must not
    # crash, and a NEW file appearing at the same path afterward must be picked up cleanly -----
    log2_path = tmp_path / "vanish.json"
    state2_path = tmp_path / "vanish-tail.json"
    log2_path.write_text("x\ny\n")
    tailer2 = LogTailer(log2_path, state2_path)
    assert tailer2.read_new_lines() == ["x", "y"]

    log2_path.unlink()
    assert tailer2.read_new_lines() == []  # vanished while open: survives silently

    log2_path.write_text("z\n")
    assert tailer2.read_new_lines() == ["z"]  # a fresh file at the same path is picked up


# --- ENOENT branch: logs, never crashes (M12) --------------------------------------------------


def test_enoent_branch_logs_and_continues(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """M12: the vanished-while-open branch (`tail.py`'s `os.stat` OSError during the inode check)
    is silent today — it must log the SAME rate-limited WARNING the unreadable-open path already
    uses (errno only, never the path or any log content, PRD §10.6), so an operator can tell a
    log file disappeared out from under a live tailer rather than inferring it from a spool/dead-
    letter gap.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("a\nb\n")
    tailer = LogTailer(log_path, state_path)
    assert tailer.read_new_lines() == ["a", "b"]

    log_path.unlink()

    with caplog.at_level(logging.WARNING):
        assert tailer.read_new_lines() == []

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected exactly one WARNING, got {len(warnings)}: {warnings}"
    assert "errno=" in warnings[0].getMessage()


# --- OpenSession.events bounded before close (new M5) ------------------------------------------


def test_open_session_events_bounded_before_close() -> None:
    """New M5 (M6 final review): `OpenSession.events` must be bounded INCREMENTALLY as each event
    arrives (cap 1's truncation applied inside `feed`, not only at `build_payload`) — an attacker
    holding one session open and streaming commands must never grow it past `max_events` in
    memory, regardless of how many events are fed before the session closes or idle-flushes
    (today, cap 1 applies only at `build_payload`, so an open session's `events` list grows
    without bound — a 412 MB host OOMs, `Restart=always` fires, and the in-flight batch is lost).

    `assembler._sessions` has no public accessor for an in-flight session's live event count; the
    memory-safety invariant under test has no other observable surface (judgment call, recorded
    in the test-author report).
    """
    assembler = SessionAssembler(idle_flush_s=900.0, max_events=5, max_payload_bytes=1_500_000)
    session_id = "bounded-open-session"

    assembler.feed(json.dumps(_event("cowrie.session.connect", 0, session_id=session_id)))
    for i in range(1, 21):
        assembler.feed(
            json.dumps(_event("cowrie.command.input", i, session_id=session_id, input="x"))
        )
        open_session = assembler._sessions[session_id]
        assert len(open_session.events) <= 5, (
            f"open session grew to {len(open_session.events)} events after {i} feeds — "
            "max_events must bound it INCREMENTALLY (in feed), not only at close"
        )


# --- unit sandboxing: the four directives present (new M8) -------------------------------------

_UNIT_PATH = (
    Path(__file__).resolve().parent.parent
    / "honeypot"
    / "shipper"
    / "sentinelbrief-shipper.service"
)


def test_unit_file_has_sandboxing_directives() -> None:
    """New M8 (M6 final review): the unit file's sandboxing set names `ProtectSystem=strict`,
    `PrivateTmp`, `NoNewPrivileges`, and `CapabilityBoundingSet=` — the first three already ship
    (`tests/test_shipper_isolation.py::test_unit_file_hardening_and_pyproject_shape`, pinned,
    checks a subset); `CapabilityBoundingSet=` is the one actually missing today. Pinned as a
    group here so a future edit cannot silently drop any of them.
    """
    unit_text = _UNIT_PATH.read_text()
    for expected in (
        "ProtectSystem=strict",
        "PrivateTmp=true",
        "NoNewPrivileges=true",
        "CapabilityBoundingSet=",
    ):
        assert expected in unit_text, f"{_UNIT_PATH} is missing {expected!r} (M6 final review M8)"
