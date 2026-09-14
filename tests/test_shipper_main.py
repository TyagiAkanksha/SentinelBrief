"""Pins `sentinelbrief_shipper.main` (m6 task-02): `Backoff`'s exponential wait, `drain`'s
stop-at-first-retry/dead-letter behavior, the outage-then-recovery guarantee (PRD §11 "never
loses a closed session"), `--once` end-to-end against the replay fixture, the config-error exit
path, and a clean SIGTERM stop.

The only fakes are `httpx.MockTransport` (the ingest seam), an injected `sleep`, and — for the
SIGTERM row — an injected `stop_event`.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package, every test below fails at
collection with `ModuleNotFoundError: No module named 'sentinelbrief_shipper'`.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
from sentinelbrief_shipper.main import Backoff, drain, main
from sentinelbrief_shipper.post import Poster
from sentinelbrief_shipper.spool import Spool

from core.schemas.alert import SessionAlert

_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"
_HMAC_SECRET = "test-secret"
_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "cowrie" / "cowrie.json"


def _poster(transport: httpx.BaseTransport) -> Poster:
    return Poster(_INGEST_URL, _HMAC_SECRET, timeout_s=5.0, transport=transport)


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "SHIPPER_INGEST_URL": _INGEST_URL,
        "INGEST_HMAC_SECRET": _HMAC_SECRET,
        "SHIPPER_LOG_PATH": str(_FIXTURE_PATH),
        "SHIPPER_STATE_DIR": str(tmp_path / "state"),
    }


def _session_id_of(body: bytes) -> str:
    session_id = json.loads(body)["session_id"]
    assert isinstance(session_id, str)
    return session_id


def _is_ordered_subsequence(expected: list[float], actual: list[float]) -> bool:
    """True when every value in `expected` appears in `actual`, in the same relative order —
    tolerant of extra values (e.g. a `poll_interval_s` sleep) interleaved between them.
    """
    it = iter(actual)
    return all(any(value == candidate for candidate in it) for value in expected)


def test_drain_stops_at_first_retryable_failure_and_keeps_order(tmp_path: Path) -> None:
    """Interfaces `drain`: it stops at the FIRST retryable failure (never skips ahead), the
    failed payload stays at the head of the spool for the next attempt, and a healed transport
    then delivers the remaining payloads in FIFO order — mutant: continuing past a retryable
    failure, or losing FIFO order on the next drain.
    """
    spool = Spool(tmp_path / "spool", max_files=10)
    body1, body2, body3 = b'{"n":1}', b'{"n":2}', b'{"n":3}'
    spool.put(body1)
    p2 = spool.put(body2)
    p3 = spool.put(body3)

    calls: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        if request.content == body1:
            return httpx.Response(202)
        raise httpx.ConnectError("boom", request=request)

    sleeps: list[float] = []
    backoff = Backoff(base_s=0.01, max_s=1.0, sleep=sleeps.append)

    delivered = drain(spool, _poster(httpx.MockTransport(handler)), backoff)

    assert delivered == 1
    assert spool.pending() == [p2, p3]
    assert calls == [body1, body2]
    assert len(sleeps) == 1

    delivered_bodies: list[bytes] = []

    def healed_handler(request: httpx.Request) -> httpx.Response:
        delivered_bodies.append(request.content)
        return httpx.Response(202)

    delivered_second = drain(spool, _poster(httpx.MockTransport(healed_handler)), backoff)

    assert delivered_second == 2
    assert delivered_bodies == [body2, body3]
    assert spool.pending() == []


def test_drain_dead_letters_4xx_and_continues(tmp_path: Path) -> None:
    """Interfaces `drain`: a permanently-rejected (non-retryable 4xx) payload is dead-lettered and
    draining CONTINUES to the next payload in the same call — mutant: stopping the whole drain on
    a dead-letter the way it stops on a retry.
    """
    spool_dir = tmp_path / "spool"
    spool = Spool(spool_dir, max_files=10)
    body1, body2 = b'{"n":1}', b'{"n":2}'
    p1 = spool.put(body1)
    p2 = spool.put(body2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.content == body1:
            return httpx.Response(401)
        return httpx.Response(202)

    backoff = Backoff(base_s=0.01, max_s=1.0, sleep=lambda _seconds: None)

    delivered = drain(spool, _poster(httpx.MockTransport(handler)), backoff)

    assert delivered == 1
    assert spool.pending() == []
    assert (spool_dir / "dead" / p1.name).exists()
    assert not (spool_dir / "dead" / p2.name).exists()


def test_outage_then_recovery_delivers_every_closed_session_in_order(tmp_path: Path) -> None:
    """PRD §11 "the shipper never loses a closed session while the ingest URL is down": replaying
    the fixture through `main` against a transport that fails the first four POST attempts then
    succeeds delivers both closed sessions (B then A — B closes first), empties the spool, and
    backs off exponentially (2, 4, 8, ... doubling per consecutive failure) — mutant: dropping a
    payload on repeated failure, delivering out of order, or a flat (non-exponential) backoff.
    """
    call_count = 0
    delivered_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count <= 4:
            raise httpx.ConnectError("boom", request=request)
        delivered_bodies.append(request.content)
        return httpx.Response(202)

    sleeps: list[float] = []

    exit_code = main(
        [],
        env=_base_env(tmp_path),
        transport=httpx.MockTransport(handler),
        max_iterations=5,
        sleep=sleeps.append,
    )

    assert exit_code == 0
    assert len(delivered_bodies) == 2
    assert [_session_id_of(b) for b in delivered_bodies] == ["b2c3d4e5f6a7", "a1b2c3d4e5f6"]
    assert list((tmp_path / "state" / "spool").glob("*.json")) == []
    assert _is_ordered_subsequence([2.0, 4.0, 8.0, 16.0], sleeps)


def test_main_once_replays_fixture_and_exits_zero(tmp_path: Path) -> None:
    """Interfaces `main --once`: one `run_once` call against a healthy transport delivers both
    closed sessions from the fixture and exits 0 — mutant: looping past a single iteration under
    `--once`, or posting a payload that fails `SessionAlert` validation.
    """
    posted: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request.content)
        return httpx.Response(202)

    exit_code = main(["--once"], env=_base_env(tmp_path), transport=httpx.MockTransport(handler))

    assert exit_code == 0
    assert len(posted) == 2
    for body in posted:
        SessionAlert.model_validate(json.loads(body))


def test_main_exit_1_names_missing_var_without_value(capsys: pytest.CaptureFixture[str]) -> None:
    """Interfaces `main`: a `ShipperConfig.from_env` `ValueError` (a required var missing) exits 1
    with one stderr line naming the missing var, and never echoes a configured value (here the
    canary URL, present but the secret is not) — mutant: a raw traceback, or leaking a configured
    value into the message.
    """
    canary_url = "https://canary.invalid/api/v1/alerts"
    env = {"SHIPPER_INGEST_URL": canary_url}

    exit_code = main(["--once"], env=env)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "INGEST_HMAC_SECRET" in captured.err
    assert canary_url not in captured.err


def test_sigterm_stops_after_current_iteration(tmp_path: Path) -> None:
    """SIGTERM row (judgment call, documented in the test-author report): a `stop_event` set
    mid-iteration (simulating SIGTERM) lets the CURRENT iteration finish — a payload is never
    half-written, since the spool writes tmp+`os.replace` — and `main` then exits 0 without
    starting another iteration — mutant: stopping mid-write, or ignoring the stop request.
    """
    stop_event = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        stop_event.set()
        return httpx.Response(202)

    exit_code = main(
        [],
        env=_base_env(tmp_path),
        transport=httpx.MockTransport(handler),
        stop_event=stop_event,
        sleep=lambda _seconds: None,
    )

    assert exit_code == 0
    spool_dir = tmp_path / "state" / "spool"
    assert not any(p.suffix == ".tmp" for p in spool_dir.rglob("*"))
