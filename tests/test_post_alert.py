"""Pins `scripts/post_alert.py`'s `main(argv, *, transport)` contract — m2 task-03.

`scripts/` has no `__init__.py`, so the module is loaded via
`importlib.util.spec_from_file_location` rather than a normal import. `main` takes an injectable
`transport` (default: real network) so these tests never touch the network, and reads
`INGEST_HMAC_SECRET` from the environment only — no dotenv dependency, no way to silently pick up
a stray `.env`. A missing secret or a missing fixture file must fail with a clean one-line stderr
message and exit code 1, never a raw traceback.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from core.signing import sign_body

REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_PATH = REPO_ROOT / "fixtures" / "alerts" / "alert4.json"


def _load_post_alert() -> ModuleType:
    """Load `scripts/post_alert.py` as a standalone module (no package `__init__.py` exists)."""
    spec = importlib.util.spec_from_file_location(
        "post_alert", REPO_ROOT / "scripts" / "post_alert.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


post_alert = _load_post_alert()


def _forbidden_transport() -> httpx.MockTransport:
    """A transport that fails the test if the script ever attempts a network call."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network should not be reached: {request.url}")

    return httpx.MockTransport(handler)


def _ok_response() -> httpx.Response:
    return httpx.Response(202, json={"id": str(uuid.uuid4()), "status": "triaged", "created": True})


def test_post_alert_signs_and_posts_exit_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INGEST_HMAC_SECRET", "s")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Signature"] == sign_body("s", request.content)
        assert request.url.path == "/api/v1/alerts"
        return _ok_response()

    exit_code = post_alert.main([str(_FIXTURE_PATH)], transport=httpx.MockTransport(handler))

    assert exit_code == 0
    assert capsys.readouterr().out.startswith("202 ")


def test_post_alert_exit_1_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_HMAC_SECRET", "s")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "nope"}})

    exit_code = post_alert.main([str(_FIXTURE_PATH)], transport=httpx.MockTransport(handler))

    assert exit_code == 1


def test_post_alert_exit_1_without_secret_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)

    exit_code = post_alert.main([str(_FIXTURE_PATH)], transport=_forbidden_transport())

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    stderr_lines = captured.err.strip().splitlines()
    assert len(stderr_lines) == 1
    assert "INGEST_HMAC_SECRET" in stderr_lines[0]
    assert "Traceback" not in captured.err


def test_post_alert_exit_1_on_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_HMAC_SECRET", "s")
    missing = REPO_ROOT / "fixtures" / "alerts" / "does-not-exist.json"

    exit_code = post_alert.main([str(missing)], transport=_forbidden_transport())

    assert exit_code == 1


def test_post_alert_url_flag_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_HMAC_SECRET", "s")
    seen_hosts: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return _ok_response()

    exit_code = post_alert.main(
        [str(_FIXTURE_PATH), "--url", "http://example.test:9"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 0
    assert seen_hosts == ["example.test"]


def test_post_alert_url_trailing_slash_joins_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `--url` with a trailing slash must not produce a doubled `//` in the request path
    (m2-final-review.md t03 M3/N2 — the fix landed at `scripts/post_alert.py:55`; this pins it).
    """
    monkeypatch.setenv("INGEST_HMAC_SECRET", "s")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/alerts"
        return _ok_response()

    exit_code = post_alert.main(
        [str(_FIXTURE_PATH), "--url", "http://example.test:9/"],
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == 0
