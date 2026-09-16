"""LogTailer (m6 task-02): a rotation-aware line follower with a persisted position.

Follows Cowrie's `cowrie.json` across its daily rotations (rename to `cowrie.json.<date>`, a
fresh `cowrie.json` created in its place) and in-place truncation, returning only complete lines
so a partial write is never delivered as a whole one. The position survives a process restart —
`<state_dir>/tail.json` is rewritten (tmp + `os.replace`) by `commit_offset()`, which
`read_new_lines()` calls itself unless the caller asked to commit later (m7 task-08, M6 final
review M11: `run_once` writes the spool FIRST and commits the offset AFTER, so delivery is
at-least-once — a crash in between re-ships a session the api dedups by fingerprint, instead of
dropping it). A cold start over a large backlog is read in `read_chunk_bytes`-sized chunks and
one call returns at most `max_batch_lines` lines (M9).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

# Fallbacks for a `LogTailer` built without the tunables; in production `main` always passes
# `ShipperConfig.read_chunk_bytes`/`max_batch_lines` (`SHIPPER_READ_CHUNK_BYTES`/
# `SHIPPER_MAX_BATCH_LINES`), which carry the same documented values.
_READ_CHUNK_BYTES_DEFAULT = 8 * 1024 * 1024
_MAX_BATCH_LINES_DEFAULT = 2000


@dataclass
class TailState:
    """The persisted tail position: an inode and a byte offset into it."""

    inode: int
    offset: int


class LogTailer:
    """Follows `path`, returning every complete line written since the last call."""

    def __init__(
        self,
        path: Path,
        state_path: Path,
        *,
        read_chunk_bytes: int = _READ_CHUNK_BYTES_DEFAULT,
        max_batch_lines: int = _MAX_BATCH_LINES_DEFAULT,
    ) -> None:
        """Set up the tailer; nothing is opened until the first `read_new_lines()` call.

        Args:
            path: The Cowrie JSON log path to follow.
            state_path: Where to persist `TailState` as JSON.
            read_chunk_bytes: Max bytes per `read()` call on the open file (M9) — a cold start
                over a multi-MB backlog never issues one unbounded read.
            max_batch_lines: Max complete lines ONE `read_new_lines()` call returns (M9); the
                rest stay buffered for the next call, so a back-ship burst is paced.
        """
        self._path = path
        self._state_path = state_path
        self._read_chunk_bytes = read_chunk_bytes
        self._max_batch_lines = max_batch_lines
        self._file: BinaryIO | None = None
        self._inode: int | None = None
        self._buffer: bytes = b""
        self._last_open_errno: int | None = None
        self._pending_state: TailState | None = None
        self._at_eof = False

    def read_new_lines(self, *, persist: bool = True) -> list[str]:
        """Return every complete line written to `path` since the last call.

        Args:
            persist: Commit the new position before returning (the default, for callers that
                have nowhere else to put the lines). `run_once` passes `False` and calls
                `commit_offset()` itself once the payloads are on disk (M11, at-least-once).

        Returns:
            Complete lines (newline stripped), in file order — `[]` when the file is missing,
            unreadable (e.g. a permission/ownership mismatch between Cowrie and the `shipper`
            user), or has grown by less than one full line. A trailing partial line stays
            buffered until its newline arrives. At most `max_batch_lines` lines are returned per
            call (M9); the rest stay buffered for the next one. On rotation (the open file's
            inode no longer matches `path`'s), this call drains the OLD file's remaining lines
            and closes it once they are exhausted — a backlog larger than one batch takes several
            calls, and the new `path` is only opened after that. A saved offset beyond a
            (truncated) file's current size seeks to 0 instead of silently skipping the file's
            new content.
            An `OSError` opening the file (I1/N1, review fix-1/fix-2) never propagates — a
            missing file (`FileNotFoundError`, e.g. Cowrie has not written yet) is always
            silent; any other `OSError` (e.g. the log's PARENT DIRECTORY, not just the file
            itself, is unsearchable by the `shipper` user) is logged once per distinct errno
            (never again until a successful open) and otherwise treated the same as missing, so
            the poll loop keeps draining the spool instead of crashing into a restart loop. The
            existence check lives INSIDE this handling (never a bare `path.exists()` ahead of
            it, which would itself raise on an unsearchable parent directory — N1). A file that
            VANISHES while held open (`os.stat` on its path fails, M12) is drained, closed and
            logged with the same rate-limited, errno-only WARNING — never the path or a line's
            content (PRD §10.6).
        """
        if self._file is not None:
            try:
                current_inode: int | None = os.stat(self._path).st_ino
            except OSError as exc:
                self._warn_once_per_errno(exc)
                current_inode = None
            if current_inode != self._inode:
                lines = self._drain_open_file()
                if self._at_eof:
                    # The old file is exhausted: close it and let the NEXT call open the new
                    # `path`. Until then (a batch cap reached mid-drain, M9) the rotated file
                    # stays open and the next call resumes draining it — closing early would
                    # discard both the buffered lines and everything still unread in it.
                    self._file.close()
                    self._file = None
                    self._inode = None
                    self._buffer = b""
                if persist:
                    self.commit_offset()
                return lines

        if self._file is None:
            try:
                self._open_at_saved_offset()
            except FileNotFoundError:
                return []
            except OSError as exc:
                self._warn_once_per_errno(exc)
                return []
            self._last_open_errno = None

        lines = self._drain_open_file()
        if persist:
            self.commit_offset()
        return lines

    def commit_offset(self) -> None:
        """Persist the position the last `read_new_lines()` call reached, if it is uncommitted.

        M11 (m7 task-08): `run_once` calls this only AFTER every payload built from those lines
        is in the spool, so a crash in between re-reads them (at-least-once) instead of skipping
        them. A second call with nothing new to commit is a no-op — the poll loop calls it once
        per iteration, and the state file must not be rewritten every idle second.
        """
        state = self._pending_state
        if state is None:
            return
        self._pending_state = None
        self._persist_state(state.inode, state.offset)

    def _warn_once_per_errno(self, exc: OSError) -> None:
        """Log `exc`'s errno at WARNING, once per distinct errno (I1; M12's vanished-while-open
        branch shares the same rate limit and the same errno-only message)."""
        errno = exc.errno if exc.errno is not None else -1
        if errno != self._last_open_errno:
            logger.warning("shipper: log file unreadable errno=%d (will retry)", errno)
            self._last_open_errno = errno

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
        """Read the open file in bounded chunks, returning at most `max_batch_lines` lines.

        Reading stops at EOF or as soon as the buffer holds a full batch (M9) — everything past
        the batch, plus any trailing partial line, stays buffered for the next call, and the
        position this computes covers ONLY the lines returned. `self._at_eof` records which of
        the two ended the read, so the rotation branch knows whether the old file is exhausted.
        """
        assert self._file is not None
        self._at_eof = False
        while self._buffer.count(b"\n") < self._max_batch_lines:
            chunk = self._file.read(self._read_chunk_bytes)
            if not chunk:
                self._at_eof = True
                break
            self._buffer += chunk

        parts = self._buffer.split(b"\n")
        partial = parts.pop()
        held_back = parts[self._max_batch_lines :]
        del parts[self._max_batch_lines :]
        self._buffer = b"".join(part + b"\n" for part in held_back) + partial

        lines = [part.decode("utf-8") for part in parts]
        if lines:
            assert self._inode is not None
            offset = self._file.tell() - len(self._buffer)
            self._pending_state = TailState(inode=self._inode, offset=offset)
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
