"""Pins `api/deps.py::require_admin_token` directly (PRD §10; m8b task-04) — a DB-less,
Redis-less probe-route suite, isolated from the full retriage route the same way
`tests/test_session_dep.py` isolates `SessionDep` from a real write path.

`from api.deps import require_admin_token` fails today (the name does not exist yet) — this
file's RED signal is a collection error naming that import, not an assertion failure.

`hmac.compare_digest` is the mandated comparison (task brief Interfaces block); this file cannot
observe *which* comparison function the implementation calls without mocking our own code
(forbidden, CONVENTIONS.md §10), so it pins the comparison's OBSERVABLE behaviour only: every
missing/malformed/wrong/empty-config case is 401, and only the exact configured bearer token
passes through.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from api.deps import require_admin_token
from api.factory import create_app
from core.config import Settings

ADMIN_TOKEN = "test-admin-token-9b1e"  # noqa: S105 -- fixture literal, not a real secret


def _probe_app(settings: Settings) -> FastAPI:
    """A DB-less app with one extra GET route gated by `require_admin_token` alone — mirrors the
    `tests/test_session_dep.py` probe-route pattern."""
    app = create_app(settings=settings)

    @app.get(
        "/_probe/admin", operation_id="probe_admin", dependencies=[Depends(require_admin_token)]
    )
    async def _probe() -> dict[str, bool]:
        return {"ok": True}

    return app


async def test_missing_authorization_header_401() -> None:
    app = _probe_app(Settings(admin_token=SecretStr(ADMIN_TOKEN)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_probe/admin")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_malformed_authorization_header_401() -> None:
    app = _probe_app(Settings(admin_token=SecretStr(ADMIN_TOKEN)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        no_scheme = await client.get("/_probe/admin", headers={"Authorization": ADMIN_TOKEN})
        wrong_scheme = await client.get(
            "/_probe/admin", headers={"Authorization": f"Basic {ADMIN_TOKEN}"}
        )
        empty_value = await client.get("/_probe/admin", headers={"Authorization": ""})

    assert no_scheme.status_code == 401
    assert wrong_scheme.status_code == 401
    assert empty_value.status_code == 401


async def test_wrong_bearer_token_401() -> None:
    app = _probe_app(Settings(admin_token=SecretStr(ADMIN_TOKEN)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/_probe/admin", headers={"Authorization": "Bearer wrong-token"}
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_empty_admin_token_configured_401_even_with_empty_bearer() -> None:
    """`ADMIN_TOKEN` unset (`Settings()`'s default `SecretStr("")`) must 401 unconditionally — a
    naive `hmac.compare_digest("", "")` is `True`, so this is the case that most directly
    exercises "empty ADMIN_TOKEN → 401 always, never fail-open" (task brief Interfaces block)."""
    app = _probe_app(Settings())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_probe/admin", headers={"Authorization": "Bearer "})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_valid_bearer_token_passes_through() -> None:
    app = _probe_app(Settings(admin_token=SecretStr(ADMIN_TOKEN)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/_probe/admin", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
