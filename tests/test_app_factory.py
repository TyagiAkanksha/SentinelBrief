"""Pins `api/factory.py::create_app` and `api/deps.py`'s request-scoped seams
(CONVENTIONS.md §5) — m2 task-02.

`create_app()` must build with no database and no env vars wired (what makes the OpenAPI export
and DB-less tests possible), every route must declare a unique `operation_id` (the frontend's
codegen keys on it), and dependencies that need something unwired (`get_session` with no
`session_factory`, `get_triage` with no `triage`) must raise at request time rather than working
silently — surfacing through the registered handlers as the generic §8 500 envelope, never a raw
traceback. `get_settings` is the one dep that always has something to return (a default,
zero-env `Settings()` when none is injected), so its test pins the happy path instead.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import TriageFn, get_session, get_settings, get_triage
from api.factory import create_app
from core.config import Settings

# Cleared so a stray value in the running shell can never make this test's "no env" claim false.
_ENV_VARS_TO_CLEAR = (
    "LLM_API_KEY",
    "DATABASE_URL",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "INGEST_HMAC_SECRET",
    "MODEL_PRICES_JSON",
    "CORS_ORIGINS",
)


def test_create_app_without_db_or_env_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)

    app = create_app()

    assert isinstance(app, FastAPI)


def test_operation_ids_unique() -> None:
    app = create_app()

    operation_ids = [route.operation_id for route in app.routes if isinstance(route, APIRoute)]

    assert operation_ids, "expected at least one APIRoute on a freshly built app (e.g. healthz)"
    assert all(operation_ids), f"every operation_id must be set: {operation_ids}"
    assert len(operation_ids) == len(set(operation_ids)), (
        f"operation_ids must be unique: {operation_ids}"
    )


async def test_get_session_raises_when_dbless() -> None:
    app = create_app()

    async def _probe(session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/_probe/session", _probe, methods=["GET"], operation_id="probe_session")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_probe/session")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}


async def test_get_settings_returns_injected(settings: Settings) -> None:
    app = create_app(settings=settings)

    async def _probe(s: Settings = Depends(get_settings)) -> dict[str, str]:
        return {"cheap_model": s.cheap_model}

    app.add_api_route("/_probe/settings", _probe, methods=["GET"], operation_id="probe_settings")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_probe/settings")

    assert response.status_code == 200
    assert response.json() == {"cheap_model": "fake-model"}


async def test_get_triage_raises_when_unwired() -> None:
    app = create_app()

    async def _probe(triage: TriageFn = Depends(get_triage)) -> dict[str, bool]:
        return {"ok": True}

    app.add_api_route("/_probe/triage", _probe, methods=["GET"], operation_id="probe_triage")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_probe/triage")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "internal error"}}
