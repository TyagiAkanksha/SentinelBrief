"""Pins `sentinelbrief_shipper.post.Poster` (m6 task-02): the status→retry mapping and the exact
headers a POST carries (signature over the exact bytes, content-type, user-agent).

The only fake is `httpx.MockTransport` at the ingest seam — never our own code.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package, every test below fails at
collection with `ModuleNotFoundError: No module named 'sentinelbrief_shipper'`.
"""

from __future__ import annotations

import httpx
import pytest
from sentinelbrief_shipper.post import Poster, PostResult
from sentinelbrief_shipper.signing import SIGNATURE_HEADER, sign_body

_INGEST_URL = "https://ingest.example.invalid/api/v1/alerts"


@pytest.mark.parametrize(
    ("handler_status", "raise_connect_error", "expected"),
    [
        (200, False, PostResult(status=200, retry=False)),
        (202, False, PostResult(status=202, retry=False)),
        (401, False, PostResult(status=401, retry=False)),
        (413, False, PostResult(status=413, retry=False)),
        (422, False, PostResult(status=422, retry=False)),
        (429, False, PostResult(status=429, retry=True)),
        (503, False, PostResult(status=503, retry=True)),
        (None, True, PostResult(status=None, retry=True)),
    ],
)
def test_status_to_retry_mapping(
    handler_status: int | None, raise_connect_error: bool, expected: PostResult
) -> None:
    """Interfaces `Poster.post`: every documented status band maps to the right
    `PostResult(status, retry)`, and a transport-level `httpx.HTTPError` (no response at all)
    maps to `(None, True)` — never raises — mutant: flipping any one band's `retry` bit, or
    letting the transport error propagate.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if raise_connect_error:
            raise httpx.ConnectError("boom", request=request)
        assert handler_status is not None
        return httpx.Response(handler_status)

    poster = Poster(
        _INGEST_URL, "test-secret", timeout_s=5.0, transport=httpx.MockTransport(handler)
    )
    try:
        result = poster.post(b'{"n":1}')
    finally:
        poster.close()

    assert result == expected


def test_post_sends_signature_over_exact_bytes_and_user_agent() -> None:
    """Interfaces `Poster.__init__`/`post`: every request carries `X-Signature` computed over the
    EXACT bytes sent (`sign_body(secret, request.content)`), `content-type: application/json`,
    and a `user-agent` identifying the shipper — mutant: signing a re-encoded/re-serialized copy
    of the payload instead of the literal bytes handed to `post`.
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)

    secret = "post-secret"
    payload = b'{"session_id":"x"}'
    poster = Poster(_INGEST_URL, secret, timeout_s=5.0, transport=httpx.MockTransport(handler))
    try:
        result = poster.post(payload)
    finally:
        poster.close()

    assert result == PostResult(status=202, retry=False)
    assert len(captured) == 1
    request = captured[0]
    assert request.headers[SIGNATURE_HEADER] == sign_body(secret, request.content)
    assert request.headers["content-type"] == "application/json"
    assert request.headers["user-agent"].startswith("sentinelbrief-shipper/")
