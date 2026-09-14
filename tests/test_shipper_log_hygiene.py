"""Widens the rule-1 negative log pin (m6 task-02 review I4): `_ATTACKER_STRINGS` in
`tests/test_shipper_assemble.py` (pinned, untouched) has no source IP and only ever exercises the
assembler; this file (1) builds its forbidden set from EVERY `src_ip`, `username`, `password`,
`input`, `url`, and `version` value in the fixture itself — so the pin can never drift from the
fixture — and (2) captures log records across a full `main(["--once"], ...)` run: the assembler,
the spool, and `drain`'s own "delivered"/"dead-lettered" lines, across the normal-delivery,
dead-letter, and retry paths.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from sentinelbrief_shipper.main import main

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "cowrie" / "cowrie.json"
_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"
_HMAC_SECRET = "test-secret"

_FORBIDDEN_FIELDS = ("src_ip", "username", "password", "input", "url", "version")


def _collect_forbidden() -> tuple[str, ...]:
    """Every attacker-controlled value in the fixture — parsed from the fixture itself, not
    hand-copied, so this pin can never silently drift from what the fixture actually contains.
    """
    values: set[str] = set()
    for line in _FIXTURE_PATH.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for field in _FORBIDDEN_FIELDS:
            value = event.get(field)
            if isinstance(value, str) and value:
                values.add(value)
    return tuple(sorted(values))


_FORBIDDEN = _collect_forbidden()


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "SHIPPER_INGEST_URL": _INGEST_URL,
        "INGEST_HMAC_SECRET": _HMAC_SECRET,
        "SHIPPER_LOG_PATH": str(_FIXTURE_PATH),
        "SHIPPER_STATE_DIR": str(tmp_path / "state"),
    }


def _assert_no_forbidden_string(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        message = record.getMessage()
        args_text = str(record.args)
        for forbidden in _FORBIDDEN:
            assert forbidden not in message, f"{forbidden!r} leaked in {message!r}"
            assert forbidden not in args_text, f"{forbidden!r} leaked in record.args {args_text!r}"


def test_no_forbidden_string_across_normal_delivery(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The full replay through `main(["--once"])` against a healthy transport (assembler + spool
    + `drain`'s "delivered" line) never logs a fixture source IP, username, password, command, URL
    or client-version banner.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    with caplog.at_level(logging.DEBUG):
        exit_code = main(
            ["--once"], env=_base_env(tmp_path), transport=httpx.MockTransport(handler)
        )

    assert exit_code == 0
    _assert_no_forbidden_string(caplog)


def test_no_forbidden_string_across_dead_letter_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The same replay against a transport that rejects everything with `401` (`drain`'s
    "dead-lettered" line) never logs a forbidden value either.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with caplog.at_level(logging.DEBUG):
        exit_code = main(
            ["--once"], env=_base_env(tmp_path), transport=httpx.MockTransport(handler)
        )

    assert exit_code == 0
    _assert_no_forbidden_string(caplog)


def test_no_forbidden_string_across_retry_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The same replay against a transport that always raises `ConnectError` (the retry/backoff
    path) never logs a forbidden value either.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with caplog.at_level(logging.DEBUG):
        exit_code = main(
            ["--once"],
            env=_base_env(tmp_path),
            transport=httpx.MockTransport(handler),
            sleep=lambda _seconds: None,
        )

    assert exit_code == 0
    _assert_no_forbidden_string(caplog)
