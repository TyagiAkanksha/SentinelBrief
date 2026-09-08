"""Unit-pins `api/deps.py::require_signature` directly against a hand-built ASGI `Request`
(m2 task-03 fix 1, controller ruling R3).

`tests/test_ingest.py` already exercises `require_signature` end to end through
`SignedRoute`/the full HTTP surface; these two tests isolate the dependency function itself —
no app router, no `SessionDep`, no DB — so a regression in the raw-body-vs-header comparison
fails here without needing the whole ingest stack to explain why. A new file (rather than
folding these into `test_ingest.py`) keeps the raw-ASGI-`Request` construction, which is a
different testing style from the HTTP-client-driven tests there, out of that file's blast radius.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from starlette.requests import Request

from api.deps import require_signature
from api.factory import create_app
from core.config import Settings
from core.errors import SignatureError
from core.signing import sign_body


def _make_request(*, app: object, header_value: bytes, body: bytes) -> Request:
    """Build a minimal ASGI `Request` carrying `header_value` under `X-Signature` and `body`."""

    async def receive() -> Mapping[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/alerts",
        "headers": [(b"x-signature", header_value)],
        "app": app,
    }
    return Request(scope, receive)


async def test_require_signature_raises_on_bad_header(settings: Settings) -> None:
    app = create_app(settings=settings)
    request = _make_request(app=app, header_value=b"sha256=00", body=b"{}")

    with pytest.raises(SignatureError):
        await require_signature(request, settings)


async def test_require_signature_passes_on_valid_header(settings: Settings) -> None:
    app = create_app(settings=settings)
    body = b"{}"
    header_value = sign_body("test-secret", body).encode()
    request = _make_request(app=app, header_value=header_value, body=body)

    assert await require_signature(request, settings) is None
