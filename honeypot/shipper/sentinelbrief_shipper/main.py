"""The shipper's entry point (m6 task-02): wires `LogTailer` -> `SessionAssembler` -> `Spool` ->
`Poster` into a poll loop, with exponential backoff on a retryable delivery failure and a clean
stop on SIGTERM/SIGINT (or an injected `stop_event`, for tests) that always finishes the current
iteration first — a spool file is never half-written (tmp + `os.replace`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import httpx

from sentinelbrief_shipper.assemble import SessionAssembler
from sentinelbrief_shipper.config import ShipperConfig
from sentinelbrief_shipper.post import Poster
from sentinelbrief_shipper.spool import Spool
from sentinelbrief_shipper.tail import LogTailer

logger = logging.getLogger(__name__)


class Backoff:
    """Exponential backoff: `min(base_s * 2**n, max_s)`, `n` = consecutive waits since reset."""

    def __init__(
        self, *, base_s: float, max_s: float, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        """Set the backoff curve and the sleep function to drive it with.

        Args:
            base_s: The delay for the first wait after a reset.
            max_s: The cap on the delay, regardless of how many consecutive waits occurred.
            sleep: The sleep function to call (tests inject a recording fake).
        """
        self._base_s = base_s
        self._max_s = max_s
        self._sleep = sleep
        self._consecutive = 0

    def wait(self) -> float:
        """Sleep for the current backoff delay and advance the consecutive-wait counter.

        Returns:
            The delay, in seconds, that was slept.
        """
        delay = min(self._base_s * (2.0**self._consecutive), self._max_s)
        self._consecutive += 1
        self._sleep(delay)
        return delay

    def reset(self) -> None:
        """Reset the consecutive-wait counter after a successful delivery."""
        self._consecutive = 0


def drain(spool: Spool, poster: Poster, backoff: Backoff) -> int:
    """Attempt every spooled payload in FIFO order; stop at the first retryable failure.

    Args:
        spool: The disk spool to drain.
        poster: The `Poster` to send each payload through.
        backoff: Waited once on a retryable failure (then draining stops so the SAME file is
            retried first next time); reset on every successful delivery.

    Returns:
        The number of payloads delivered (removed from the spool) in this call. A permanently
        rejected (non-retryable 4xx) payload is dead-lettered and does NOT stop the drain.
    """
    delivered = 0
    for path in spool.pending():
        payload = path.read_bytes()
        result = poster.post(payload)
        if result.retry:
            backoff.wait()
            return delivered
        status = result.status
        assert status is not None
        if 200 <= status < 300:
            spool.remove(path)
            backoff.reset()
            delivered += 1
            logger.info(
                "shipper: delivered session_id=%s status=%d bytes=%d",
                _session_id_for_log(payload),
                status,
                len(payload),
            )
        else:
            spool.dead(path, status=status)
    return delivered


def _session_id_for_log(payload: bytes) -> str:
    """`session_id` out of `payload` for the one-line "delivered" log record.

    PRD §10.6 rule 1: `session_id` is a Cowrie-generated hex id, not attacker-controlled, so it
    is the one per-session field this line may carry. Never raises — a payload that is not a
    `SessionAlert` (e.g. in a unit test) logs `"unknown"` instead of crashing the drain loop.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return "unknown"
    session_id = data.get("session_id") if isinstance(data, dict) else None
    return session_id if isinstance(session_id, str) else "unknown"


@dataclass(frozen=True)
class RunOnceResult:
    """One `run_once` iteration's outcome (review PC1/I3).

    `main`'s loop needs both numbers: it sleeps only when `lines_read == 0` (nothing new to
    tail), which `delivered` alone cannot convey — a busy iteration that tailed new lines but
    delivered nothing (e.g. every payload is still spooled behind a retryable failure) must NOT
    sleep, since the tailer may already have more to read.
    """

    lines_read: int
    delivered: int


def run_once(
    tailer: LogTailer,
    assembler: SessionAssembler,
    spool: Spool,
    poster: Poster,
    backoff: Backoff,
) -> RunOnceResult:
    """One full iteration: tail new lines, feed the assembler, spool any payloads, then drain.

    `main`'s loop calls this — never inlines the same sequence (review I3: a duplicated loop body
    is dead code no mutation of `run_once` could ever fail a test on).

    Returns:
        `RunOnceResult(lines_read, delivered)`.
    """
    lines = tailer.read_new_lines()
    _spool_new_payloads(assembler, spool, lines)
    delivered = drain(spool, poster, backoff)
    return RunOnceResult(lines_read=len(lines), delivered=delivered)


def _spool_new_payloads(assembler: SessionAssembler, spool: Spool, lines: list[str]) -> None:
    """Feed `lines` through `assembler`, spooling every payload it (or an idle flush) produces."""
    for line in lines:
        for payload in assembler.feed(line):
            spool.put(payload)
    for payload in assembler.flush_idle():
        spool.put(payload)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    max_iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stop_event: threading.Event | None = None,
) -> int:
    """Entry point: `sentinelbrief-shipper [--once] [--state-dir DIR] [--log-path PATH]`.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        env: The environment mapping `ShipperConfig.from_env` reads; `None` reads `os.environ`.
        transport: An `httpx.BaseTransport` to route the `Poster`'s requests through instead of
            the real network — tests inject `httpx.MockTransport` through this.
        max_iterations: Stop after this many loop iterations (tests); `None` loops until
            SIGTERM/SIGINT or `stop_event` is set.
        sleep: The sleep function `Backoff` and the poll-interval wait use (tests inject a
            recording fake).
        stop_event: When given, checked once per loop iteration in addition to the real
            SIGTERM/SIGINT handlers this installs — the current iteration always finishes first,
            so a payload is never half-written (the spool writes tmp + `os.replace`).

    Returns:
        `0` on a clean stop (`--once`, `max_iterations` exhausted, `stop_event` set, or
        SIGTERM/SIGINT); `1` on a config error (one stderr line naming the missing/invalid
        variable, never a configured value) or an unreadable state directory.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(prog="sentinelbrief-shipper")
    parser.add_argument("--once", action="store_true", help="run one iteration and exit")
    parser.add_argument("--state-dir", default=None, help="override SHIPPER_STATE_DIR")
    parser.add_argument("--log-path", default=None, help="override SHIPPER_LOG_PATH")
    args = parser.parse_args(argv)

    try:
        config = ShipperConfig.from_env(env if env is not None else os.environ)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state_dir = Path(args.state_dir) if args.state_dir else config.state_dir
    log_path = Path(args.log_path) if args.log_path else config.log_path

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tailer = LogTailer(log_path, state_dir / "tail.json")
        assembler = SessionAssembler(
            idle_flush_s=config.idle_flush_s,
            max_events=config.max_events,
            max_payload_bytes=config.max_payload_bytes,
        )
        spool = Spool(state_dir / "spool", max_files=config.spool_max_files)
        poster = Poster(
            config.ingest_url,
            config.hmac_secret,
            timeout_s=config.post_timeout_s,
            transport=transport,
        )
        backoff = Backoff(base_s=config.backoff_base_s, max_s=config.backoff_max_s, sleep=sleep)
    except OSError as exc:
        # M7 (review fix-1): every collaborator built here lives under `state_dir` (Spool's own
        # `mkdir`s included) except the network-only Poster/Backoff, which never raise OSError at
        # construction — so one guard around the whole block is the right scope, not just the
        # first `mkdir`.
        print(f"error: state dir unreadable: {exc}", file=sys.stderr)
        return 1

    stop_requested = threading.Event()

    def _request_stop(signum: int, frame: FrameType | None) -> None:
        stop_requested.set()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    try:
        iterations = 0
        while True:
            result = run_once(tailer, assembler, spool, poster, backoff)
            iterations += 1

            if args.once:
                break
            if max_iterations is not None and iterations >= max_iterations:
                break
            if stop_event is not None and stop_event.is_set():
                break
            if stop_requested.is_set():
                break
            if result.lines_read == 0:
                sleep(config.poll_interval_s)
        return 0
    finally:
        poster.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
