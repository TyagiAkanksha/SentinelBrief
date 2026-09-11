"""LogTailer (m6 task-02): a rotation-aware line follower with a persisted position.

Follows Cowrie's `cowrie.json` across its daily rotations (rename to `cowrie.json.<date>`, a
fresh `cowrie.json` created in its place) and in-place truncation, returning only complete lines
so a partial write is never delivered as a whole one. The position survives a process restart —
`<state_dir>/tail.json` is rewritten (tmp + `os.replace`) after every call that returned a line.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)


@dataclass
class TailState:
    """The persisted tail position: an inode and a byte offset into it."""

    inode: int
    offset: int


class LogTailer:
    """Follows `path`, returning every complete line written since the last call."""

    def __init__(self, path: Path, state_path: Path) -> None:
        """Set up the tailer; nothing is opened until the first `read_new_lines()` call.

        Args:
            path: The Cowrie JSON log path to follow.
            state_path: Where to persist `TailState` as JSON.
        """
        self._path = path
        self._state_path = state_path
        self._file: BinaryIO | None = None
        self._inode: int | None = None
        self._buffer: bytes = b""
        self._last_open_errno: int | None = None

    def read_new_lines(self) -> list[str]:
        """Return every complete line written to `path` since the last call.

        Returns:
            Complete lines (newline stripped), in file order — `[]` when the file is missing,
            unreadable (e.g. a permission/ownership mismatch between Cowrie and the `shipper`
            user), or has grown by less than one full line. A trailing partial line stays
            buffered until its newline arrives. On rotation (the open file's inode no longer
            matches `path`'s), this call drains the OLD file's remaining lines and closes it; the
            new `path` is only opened on the NEXT call. A saved offset beyond a (truncated)
            file's current size seeks to 0 instead of silently skipping the file's new content.
            An `OSError` opening the file (I1/N1, review fix-1/fix-2) never propagates — a
            missing file (`FileNotFoundError`, e.g. Cowrie has not written yet) is always
            silent; any other `OSError` (e.g. the log's PARENT DIRECTORY, not just the file
            itself, is unsearchable by the `shipper` user) is logged once per distinct errno
            (never again until a successful open) and otherwise treated the same as missing, so
            the poll loop keeps draining the spool instead of crashing into a restart loop. The
            existence check lives INSIDE this handling (never a bare `path.exists()` ahead of
            it, which would itself raise on an unsearchable parent directory — N1).
        """
        if self._file is not None:
            try:
                current_inode: int | None = os.stat(self._path).st_ino
            except OSError:
                current_inode = None
            if current_inode != self._inode:
                lines = self._drain_open_file()
                self._file.close()
                self._file = None
                self._inode = None
                self._buffer = b""
                return lines

        if self._file is None:
            try:
                self._open_at_saved_offset()
            except FileNotFoundError:
                return []
            except OSError as exc:
                errno = exc.errno if exc.errno is not None else -1
                if errno != self._last_open_errno:
                    logger.warning("shipper: log file unreadable errno=%d (will retry)", errno)
                    self._last_open_errno = errno
                return []
            self._last_open_errno = None

        return self._drain_open_file()

    def _open_at_saved_offset(self) -> None:
        """Open `self._path`, seeking to the persisted offset when it still applies."""
        file = self._path.open("rb")
        inode = os.stat(self._path).st_ino
        state = self._load_state()
        if state is not None and state.inode == inode:
            size = self._path.stat().st_size
            file.seek(state.offset if state.offset <= size else 0)
        self._file = file
        self._inode = inode

    def _drain_open_file(self) -> list[str]:
        """Read the open file to EOF, returning complete lines and buffering any partial one."""
        assert self._file is not None
        data = self._file.read()
        parts = (self._buffer + data).split(b"\n")
        self._buffer = parts.pop()
        lines = [part.decode("utf-8") for part in parts]
        if lines:
            assert self._inode is not None
            offset = self._file.tell() - len(self._buffer)
            self._persist_state(self._inode, offset)
        return lines

    def _load_state(self) -> TailState | None:
        """Load the persisted `TailState`, or `None` when absent/unreadable."""
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text())
            return TailState(inode=int(data["inode"]), offset=int(data["offset"]))
        except (OSError, ValueError, KeyError):
            return None

    def _persist_state(self, inode: int, offset: int) -> None:
        """Write `TailState(inode, offset)` atomically (tmp file + `os.replace`)."""
        tmp_path = self._state_path.with_name(self._state_path.name + ".tmp")
        tmp_path.write_text(json.dumps({"inode": inode, "offset": offset}))
        os.replace(tmp_path, self._state_path)
