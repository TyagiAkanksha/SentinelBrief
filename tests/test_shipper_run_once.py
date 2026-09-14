"""Pins `run_once`/`RunOnceResult` and that `main`'s loop actually calls `run_once` (m6 task-02
review I3/PC1, ruling R7): the brief now defines `run_once(...) -> RunOnceResult(lines_read,
delivered)`, and `main` MUST go through it rather than inlining the same tail/feed/spool/drain
sequence — a fix round 1 file (the eight test-author files stay pinned/untouched).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import sentinelbrief_shipper.main as shipper_main
from sentinelbrief_shipper.assemble import SessionAssembler
from sentinelbrief_shipper.main import Backoff, RunOnceResult, main, run_once
from sentinelbrief_shipper.post import Poster
from sentinelbrief_shipper.spool import Spool
from sentinelbrief_shipper.tail import LogTailer

_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"
_HMAC_SECRET = "test-secret"

_ONE_CLOSED_SESSION = (
    '{"eventid":"cowrie.session.connect","timestamp":"2026-09-08T03:10:00.000000Z",'
    '"session":"s1","src_ip":"198.51.100.40","sensor":"hp-test-01"}\n'
    '{"eventid":"cowrie.session.closed","timestamp":"2026-09-08T03:10:05.000000Z",'
    '"session":"s1","src_ip":"198.51.100.40","sensor":"hp-test-01","duration_ms":5000}\n'
)


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "SHIPPER_INGEST_URL": _INGEST_URL,
        "INGEST_HMAC_SECRET": _HMAC_SECRET,
        "SHIPPER_STATE_DIR": str(tmp_path / "state"),
    }


def test_run_once_delivers_and_reports_lines_read(tmp_path: Path) -> None:
    """`run_once` over a tailer that has one closed session available, against a transport that
    accepts it, returns `RunOnceResult(lines_read=<n>, delivered=1)`; a second call (nothing new
    to tail, nothing left to drain) returns `RunOnceResult(0, 0)`.
    """
    log_path = tmp_path / "cowrie.json"
    log_path.write_text(_ONE_CLOSED_SESSION)
    tailer = LogTailer(log_path, tmp_path / "tail.json")
    assembler = SessionAssembler(idle_flush_s=900.0, max_events=2000, max_payload_bytes=1_500_000)
    spool = Spool(tmp_path / "spool", max_files=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    poster = Poster(
        _INGEST_URL, _HMAC_SECRET, timeout_s=5.0, transport=httpx.MockTransport(handler)
    )
    backoff = Backoff(base_s=0.01, max_s=1.0, sleep=lambda _seconds: None)

    result = run_once(tailer, assembler, spool, poster, backoff)

    assert isinstance(result, RunOnceResult)
    assert result.lines_read == 2
    assert result.delivered == 1

    second = run_once(tailer, assembler, spool, poster, backoff)

    assert second == RunOnceResult(0, 0)

    poster.close()


def test_main_goes_through_run_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`main(["--once"], ...)` calls the module-level `run_once` exactly once — proving the loop
    is wired to it rather than inlining an equivalent sequence (review I3).
    """
    calls: list[tuple[object, ...]] = []

    def recorder(
        tailer: LogTailer,
        assembler: SessionAssembler,
        spool: Spool,
        poster: Poster,
        backoff: Backoff,
    ) -> RunOnceResult:
        calls.append((tailer, assembler, spool, poster, backoff))
        return RunOnceResult(lines_read=0, delivered=0)

    monkeypatch.setattr(shipper_main, "run_once", recorder)

    exit_code = main(["--once"], env=_base_env(tmp_path))

    assert exit_code == 0
    assert len(calls) == 1
