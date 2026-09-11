"""Pins `api/main.py`'s fail-fast wiring (CONVENTIONS.md §5) — m2 task-02 fix 2, m2 task-04;
trimmed and extended at m5 task-01 (queue split).

`api/main.py` is module-level wiring: importing it with a required secret empty must raise
`ConfigError` naming exactly which one is missing, before ever touching the database or the
queue; importing it with every required value present must succeed and expose a real `FastAPI`
`app` with a real `enqueue` callable and a real `ArqRedis` client wired (`app.state.enqueue`,
`app.state.redis`). `api/main.py` no longer builds an LLM client or a `TriagePipeline` at all —
`app.state.triage` no longer exists, and the `LLM_API_KEY`/`CHEAP_MODEL`/unpriced-model pins moved
to `tests/test_worker_main.py` (spine M5-b): the worker is the only process that ever calls the
LLM now (PRD §10.1). `REDIS_URL` joins `DATABASE_URL`/`INGEST_HMAC_SECRET` as a required-nonempty
guard — the ingest route cannot enqueue without it.

`test_api_main_never_imports_worker_or_core_llm` is contract 3's ritual pin, generalized from the
M2-era `tests/test_inline_triage.py::test_api_routes_never_import_worker` (moved here): contract
3's `ignore_imports` exception is deleted in this task, so `api.main` itself — not just the route
layer — must never pull `worker` or `core.llm` into `sys.modules`. Runs in a fresh subprocess so a
`worker` import anywhere else in the test session can never mask a regression.

No DB/Redis connection is needed here (`make_engine`/`make_redis` never connect eagerly).
`.env`-leak guard: `core.config.Settings.model_config` already sets `env_file=None` (verified
below), so `Settings()` never reads a real `.env` file regardless of `cwd` — the only leak vector
left is real process environment variables, which `monkeypatch.delenv`/`setenv` fully control for
the secrets under test.

m5 task-05 adds `test_api_main_installs_a_redis_cache`: `app.state.cache` is a `RedisTTLCache`
over the same `redis_client` `app.state.redis` uses, backing the list/stats response cache
(m3 task-02's `TTLCache` seam) instead of the default `InMemoryTTLCache`.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from arq.connections import ArqRedis
from fastapi import FastAPI

from core.cache import RedisTTLCache
from core.config import Settings
from core.errors import ConfigError

# Belt-and-suspenders: if `Settings` ever grows a real `env_file`, these tests would need a cwd
# guard too (`monkeypatch.chdir(tmp_path)`) to stay isolated from a developer's real `.env`.
assert Settings.model_config.get("env_file") is None, (
    "Settings.model_config now reads a real .env file — these tests need a cwd guard"
)


def _reset_api_main() -> None:
    """Drop any cached `api.main` module so the next import re-runs its top-level wiring."""
    sys.modules.pop("api.main", None)


def test_missing_database_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    _reset_api_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("api.main")
        assert "DATABASE_URL" in str(exc_info.value)
    finally:
        _reset_api_main()


def test_missing_ingest_hmac_secret_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    _reset_api_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("api.main")
        assert "INGEST_HMAC_SECRET" in str(exc_info.value)
    finally:
        _reset_api_main()


def test_missing_redis_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("INGEST_HMAC_SECRET", "test-secret")
    _reset_api_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("api.main")
        assert "REDIS_URL" in str(exc_info.value)
    finally:
        _reset_api_main()


def test_required_values_present_builds_app_with_enqueue_and_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("INGEST_HMAC_SECRET", "test-secret")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    _reset_api_main()

    try:
        module = importlib.import_module("api.main")
        assert isinstance(module.app, FastAPI)
        assert callable(module.app.state.enqueue)
        assert isinstance(module.app.state.redis, ArqRedis)
        assert not hasattr(module.app.state, "triage")
    finally:
        _reset_api_main()


def test_api_main_installs_a_redis_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("INGEST_HMAC_SECRET", "test-secret")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    _reset_api_main()

    try:
        module = importlib.import_module("api.main")
        assert isinstance(module.app.state.cache, RedisTTLCache)
    finally:
        _reset_api_main()


def test_api_main_never_imports_worker_or_core_llm() -> None:
    """Contract 3 has no `ignore_imports` exception left (CONVENTIONS.md §2): `api.main` must
    never pull `worker` or `core.llm` into `sys.modules`, in-process or transitively. Generalized
    from the M2-era `test_api_routes_never_import_worker` (`tests/test_inline_triage.py`), which
    only checked the route layer while `api.main` itself still carried the documented exception.
    """
    script = (
        "import importlib, os, sys\n"
        "os.environ['DATABASE_URL'] = 'postgresql://u:p@localhost:5432/x'\n"
        "os.environ['INGEST_HMAC_SECRET'] = 'test-secret'\n"
        "os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'\n"
        "importlib.import_module('api.main')\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if m == 'worker' or m.startswith('worker.') or m == 'core.llm'\n"
        ")\n"
        "assert not leaked, leaked\n"
    )
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(repo_root)
    )
    assert result.returncode == 0, result.stderr
