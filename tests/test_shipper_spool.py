"""Pins `sentinelbrief_shipper.spool.Spool` (m6 task-02): FIFO ordering with atomic writes, the
disk-protection drop-oldest path, and dead-lettering.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package, every test below fails at
collection with `ModuleNotFoundError: No module named 'sentinelbrief_shipper'`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sentinelbrief_shipper.spool import Spool


def test_put_pending_fifo_and_atomic_names(tmp_path: Path) -> None:
    """Interfaces `Spool.put`/`pending`: three puts come back from `pending()` in put order, no
    `.tmp` file is ever left behind (write-then-`os.replace`), and `remove` drops exactly one —
    mutant: sorting by mtime instead of name (flaky under fast writes), or leaving the temp file.
    """
    spool = Spool(tmp_path / "spool", max_files=10)

    p1 = spool.put(b'{"n":1}')
    p2 = spool.put(b'{"n":2}')
    p3 = spool.put(b'{"n":3}')

    assert spool.pending() == [p1, p2, p3]
    assert list((tmp_path / "spool").glob("*.tmp")) == []

    spool.remove(p2)

    assert spool.pending() == [p1, p3]


def test_spool_full_drops_oldest_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Interfaces `Spool.put`: once `pending()` would exceed `max_files`, the OLDEST payload is
    dropped (never the newest) and a WARNING names `max_files` — mutant: dropping the newest
    payload, or never enforcing the cap at all.
    """
    spool = Spool(tmp_path / "spool", max_files=2)
    p1 = spool.put(b'{"n":1}')
    p2 = spool.put(b'{"n":2}')

    with caplog.at_level(logging.WARNING):
        p3 = spool.put(b'{"n":3}')

    assert p1 not in spool.pending()
    assert spool.pending() == [p2, p3]
    assert any(
        "spool full" in record.getMessage()
        and "max_files=2" in record.getMessage()
        and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_dead_moves_file_and_logs_status(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Interfaces `Spool.dead`: the file moves under `dead/`, disappears from `pending()`, and the
    WARNING carries the status code plus the file's NAME only (never the directory/full path) —
    mutant: leaving the file in `pending()`, or logging the full path.
    """
    spool_dir = tmp_path / "spool"
    spool = Spool(spool_dir, max_files=10)
    p1 = spool.put(b'{"n":1}')

    with caplog.at_level(logging.WARNING):
        spool.dead(p1, status=401)

    assert p1 not in spool.pending()
    dead_path = spool_dir / "dead" / p1.name
    assert dead_path.exists()
    assert not p1.exists()

    message = next(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "status=401" in message
    assert p1.name in message
    assert str(spool_dir) not in message
