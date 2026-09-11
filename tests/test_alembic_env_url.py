"""Pins `alembic/env.py::_database_url` refusing an empty URL with a `ConfigError` naming
`DATABASE_URL` (M2 final review, plan defect 2; CONVENTIONS.md §6: `alembic/env.py` is
production wiring) — m5 task-05.

Today, `alembic upgrade` hands an empty URL straight to SQLAlchemy when neither `alembic.ini`'s
`sqlalchemy.url` nor `DATABASE_URL`/`TEST_DATABASE_URL` is set, which surfaces as SQLAlchemy's own
`ArgumentError`/`NoSuchModuleError` — a confusing failure for anyone running the migration by hand
without an exported URL. This test never touches a real database (no `tmp_schema`): a truly empty
URL must fail before any connection attempt is made.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config

from core.errors import ConfigError

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_upgrade_with_no_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("MIGRATE_SCHEMA", raising=False)

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", "")

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        upgrade(cfg, "head")
