"""Pins that `api/main.py`'s `enqueue` closure really calls `core.queue.enqueue_triage` on the
wired client (m5 task-01 fix-1, review finding M1) — not a closure whose body silently returns
without ever touching Redis. `app.state.enqueue` is normally only invoked through the ingest
route, which the DB-less test suite never drives end to end; this calls the closure directly
against a Redis that refuses the connection, so a `QueueUnavailableError` can only be raised by a
real `enqueue_triage` call reaching a real (if unreachable) Redis client.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from types import ModuleType

import pytest

from core.errors import QueueUnavailableError


def _reset_api_main() -> None:
    """Drop any cached `api.main` module so the next import re-runs its top-level wiring
    (mirrors `tests/test_api_main.py::_reset_api_main`; test files never import from each
    other, so this is duplicated on purpose)."""
    sys.modules.pop("api.main", None)


async def test_api_main_enqueue_closure_reaches_enqueue_triage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`app.state.enqueue` must really call `core.queue.enqueue_triage` on the wired client:
    point REDIS_URL at a refusing port and require the typed error, not a silent no-op."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("INGEST_HMAC_SECRET", "test-secret")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # port 1 refuses immediately
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_S", "0.5")
    _reset_api_main()

    module: ModuleType | None = None
    try:
        module = importlib.import_module("api.main")

        with pytest.raises(QueueUnavailableError):
            await module.app.state.enqueue(uuid.uuid4())
    finally:
        if module is not None:
            await module.redis_client.aclose()
        _reset_api_main()
