"""Pins `api/main.py`'s fail-fast wiring (CONVENTIONS.md §5) — m2 task-02 fix 2.

`api/main.py` is module-level wiring: importing it with a required secret empty must raise
`ConfigError` naming exactly which one is missing, before ever touching the database or building
the app; importing it with both secrets present must succeed and expose a real `FastAPI` `app`.

No DB is needed here (`make_engine` never connects eagerly). `.env`-leak guard: `core.config
.Settings.model_config` already sets `env_file=None` (verified below), so `Settings()` never
reads a real `.env` file regardless of `cwd` — the only leak vector left is real process
environment variables, which `monkeypatch.delenv`/`setenv` fully control for the two secrets
under test.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi import FastAPI

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
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    _reset_api_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("api.main")
        assert "INGEST_HMAC_SECRET" in str(exc_info.value)
    finally:
        _reset_api_main()


def test_both_secrets_present_builds_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("INGEST_HMAC_SECRET", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    monkeypatch.setenv("INGEST_HMAC_SECRET", "test-secret")
    _reset_api_main()

    try:
        module = importlib.import_module("api.main")
        assert isinstance(module.app, FastAPI)
    finally:
        _reset_api_main()
