"""Pins `sentinelbrief_shipper.tail.LogTailer` (m6 task-02): complete-line buffering, persisted
position across restarts, rotation handling, truncation, and a missing log file.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package, every test below fails at
collection with `ModuleNotFoundError: No module named 'sentinelbrief_shipper'`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sentinelbrief_shipper.tail import LogTailer


def test_returns_complete_lines_and_buffers_partial(tmp_path: Path) -> None:
    """Interfaces `LogTailer.read_new_lines` step 2: a trailing partial line stays buffered until
    its newline arrives — mutant: returning the partial line early, or dropping it.
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text("a\nb\npartial")
    tailer = LogTailer(log_path, tmp_path / "tail.json")

    assert tailer.read_new_lines() == ["a", "b"]

    with log_path.open("a") as f:
        f.write("-tail\n")

    assert tailer.read_new_lines() == ["partial-tail"]


def test_position_survives_restart(tmp_path: Path) -> None:
    """Interfaces `LogTailer`/`TailState`: a fresh `LogTailer` over the same state file resumes at
    the persisted offset — mutant: dropping the `TailState` persist so a restart re-delivers
    `a`/`b`.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("a\nb\n")

    first = LogTailer(log_path, state_path)
    assert first.read_new_lines() == ["a", "b"]

    with log_path.open("a") as f:
        f.write("c\n")

    second = LogTailer(log_path, state_path)
    assert second.read_new_lines() == ["c"]


def test_rotation_drains_old_file_then_follows_new(tmp_path: Path) -> None:
    """Interfaces `LogTailer.read_new_lines` step 3: on rotation, the call that first observes a
    changed inode drains the OLD file's remaining lines and closes it; the NEXT call opens the
    new `cowrie.json` at offset 0 — mutant: following the new file immediately and losing the
    old file's trailing line, or never picking up the new file at all.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("one\ntwo\n")
    tailer = LogTailer(log_path, state_path)
    assert tailer.read_new_lines() == ["one", "two"]

    with log_path.open("a") as f:
        f.write("three\n")

    rotated_path = tmp_path / "cowrie.json.2026-09-11"
    os.rename(log_path, rotated_path)
    log_path.write_text("four\n")

    assert tailer.read_new_lines() == ["three"]
    assert tailer.read_new_lines() == ["four"]

    state = json.loads(state_path.read_text())
    assert state["inode"] == os.stat(log_path).st_ino


def test_truncation_restarts_at_zero(tmp_path: Path) -> None:
    """Interfaces `LogTailer.read_new_lines` step 1: a saved offset beyond the (truncated) file's
    current size seeks to 0 on the next open rather than reading nothing — mutant: leaving the
    stale offset in place so the truncated file's own new content is silently skipped.
    """
    log_path = tmp_path / "cowrie.json"
    state_path = tmp_path / "tail.json"
    log_path.write_text("aaaaaaaaaa\n")
    first = LogTailer(log_path, state_path)
    assert first.read_new_lines() == ["aaaaaaaaaa"]

    log_path.write_text("z\n")  # truncate in place: same inode, smaller size

    second = LogTailer(log_path, state_path)
    assert second.read_new_lines() == ["z"]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """Interfaces `LogTailer.read_new_lines` step 1: a missing `path` (Cowrie has not written yet)
    returns `[]` silently — mutant: raising `FileNotFoundError` instead of returning empty.
    """
    missing_path = tmp_path / "does-not-exist.json"
    tailer = LogTailer(missing_path, tmp_path / "tail.json")

    assert tailer.read_new_lines() == []
