"""Spool (m6 task-02): a disk-backed FIFO queue every payload is written to BEFORE the first POST
attempt, so an ingest outage of any length loses no closed session (PRD §11).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class Spool:
    """A FIFO directory of `*.json` payload files, with atomic writes and a `dead/` sink."""

    def __init__(self, directory: Path, *, max_files: int) -> None:
        """Create `directory` and `directory/dead` if they do not already exist.

        Args:
            directory: The spool's root directory.
            max_files: The max number of pending (non-dead) files kept; `put` drops the OLDEST
                pending file once this would be exceeded — a disk-protection backstop, never the
                newest.
        """
        self._directory = directory
        self._dead_dir = directory / "dead"
        self._max_files = max_files
        self._directory.mkdir(parents=True, exist_ok=True)
        self._dead_dir.mkdir(parents=True, exist_ok=True)

    def put(self, payload: bytes) -> Path:
        """Write `payload` to a new spool file, atomically (tmp file + `os.replace`).

        Args:
            payload: The exact signable bytes to persist.

        Returns:
            The path of the newly written spool file.
        """
        pending = self.pending()
        if len(pending) >= self._max_files:
            pending[0].unlink()
            logger.warning(
                "shipper: spool full, oldest payload dropped (max_files=%d)", self._max_files
            )

        digest = hashlib.sha256(payload).hexdigest()[:8]
        name = f"{time.time_ns():020d}-{digest}.json"
        dest = self._directory / name
        tmp_path = self._directory / f"{name}.tmp"
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, dest)
        return dest

    def pending(self) -> list[Path]:
        """Every non-dead-lettered spool file, in FIFO (name-sorted) order."""
        return sorted(self._directory.glob("*.json"))

    def remove(self, path: Path) -> None:
        """Delete a delivered payload's spool file.

        Args:
            path: The spool file to remove.
        """
        path.unlink()

    def dead(self, path: Path, *, status: int) -> None:
        """Move `path` into `dead/`, unchanged — a payload the api permanently rejected.

        Args:
            path: The spool file to dead-letter.
            status: The HTTP status the api returned for this payload.
        """
        os.replace(path, self._dead_dir / path.name)
        logger.warning("shipper: payload dead-lettered status=%d file=%s", status, path.name)
