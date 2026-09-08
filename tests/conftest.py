"""Shared test fixtures: throwaway-schema DB fixtures and the skip-by-fixture-name hook.

CONVENTIONS.md §10: DB tests run against a fresh Postgres schema
(`sentinelbrief_test_<hex8>`), migrated to head through the production engine factory
(`core.db.make_engine`) and Alembic, then dropped on teardown. When `TEST_DATABASE_URL` is
unset, any test that requests `tmp_schema`, `db_engine`, `db_session_factory` or `db_session`
is skipped **by fixture name** in collection — a skip is recorded (visible in `-q` output),
never a silent omission (m2 task-01).

`core.db`, `psycopg` and `alembic` are imported **lazily inside the fixtures**, not at module
top, so this file (and every non-DB test in the suite) can be collected before task-01's
implementation lands those packages/modules.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from core.config import ModelPrice, Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# Fixture names that require a real database — any test requesting one of these is skipped
# (not silently dropped) when TEST_DATABASE_URL is unset.
_DB_FIXTURE_NAMES = {"tmp_schema", "db_engine", "db_session_factory", "db_session"}

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip DB-fixture tests when `TEST_DATABASE_URL` is unset (CONVENTIONS.md §10).

    Skips **by fixture name** rather than by test module/marker, so any present or future test
    that merely requests one of `_DB_FIXTURE_NAMES` is covered automatically. A skip is recorded
    in `-q` output — a green run without the env var exported must never look like DB coverage.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip_no_db = pytest.mark.skip(reason="TEST_DATABASE_URL unset")
    for item in items:
        if _DB_FIXTURE_NAMES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(skip_no_db)


def _require_test_database_url() -> str:
    """Return `TEST_DATABASE_URL`, or skip the current test if it is unset.

    Belt-and-suspenders alongside `pytest_collection_modifyitems`: the collection hook already
    skips before fixture setup runs, but a fixture must never attempt a real connection if it is
    somehow invoked without the env var present.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL unset")
    return url


@pytest.fixture
def tmp_schema() -> Iterator[tuple[str, str]]:
    """A throwaway, migrated Postgres schema: yields `(database_url, schema_name)`.

    **Sync on purpose:** `alembic/env.py` calls `asyncio.run(...)`, which cannot nest inside
    pytest-asyncio's already-running event loop (CONVENTIONS.md §10). Creates
    `sentinelbrief_test_<hex8>` through a plain autocommit `psycopg` connection, runs
    `alembic upgrade head` into it via the production `Config`/`command.upgrade` path with
    `MIGRATE_SCHEMA` set to the new schema, yields, then drops the schema unconditionally.
    """
    import psycopg
    from alembic.command import upgrade
    from alembic.config import Config

    database_url = _require_test_database_url()
    schema = f"sentinelbrief_test_{secrets.token_hex(4)}"

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')

    try:
        alembic_cfg = Config(str(_ALEMBIC_INI))
        # `Config` is configparser-backed and treats a bare "%" as an interpolation token; a
        # percent-encoded password in the URL would otherwise raise on `set_main_option`.
        # Doubling it is configparser's own escape sequence.
        alembic_cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        previous_migrate_schema = os.environ.get("MIGRATE_SCHEMA")
        os.environ["MIGRATE_SCHEMA"] = schema
        try:
            upgrade(alembic_cfg, "head")
        finally:
            if previous_migrate_schema is None:
                os.environ.pop("MIGRATE_SCHEMA", None)
            else:
                os.environ["MIGRATE_SCHEMA"] = previous_migrate_schema

        yield database_url, schema
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
async def db_engine(tmp_schema: tuple[str, str]) -> AsyncIterator[AsyncEngine]:
    """An `AsyncEngine` pinned to `tmp_schema`'s search_path, disposed after the test."""
    from core.db import make_engine

    database_url, schema = tmp_schema
    engine = make_engine(database_url, schema=schema)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """The production `async_sessionmaker`, bound to `db_engine`."""
    from core.db import make_session_factory

    return make_session_factory(db_engine)


@pytest.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One `AsyncSession` per test, closed after the test."""
    async with db_session_factory() as session:
        yield session


@pytest.fixture
def settings() -> Settings:
    """A DB-less `Settings` wired for the API tests (m2 task-02): LLM fields, an ingest HMAC
    secret and a CORS origin, on top of the task-01 fields.

    `ingest_hmac_secret` and `cors_origins` are `Settings` fields since `76a7a62` (m2 task-02);
    both are exercised directly by the ingest/CORS tests that request this fixture.
    """
    return Settings(
        ingest_hmac_secret=SecretStr("test-secret"),
        cheap_model="fake-model",
        model_prices_json={
            "fake-model": ModelPrice(input_per_mtok=Decimal("0"), output_per_mtok=Decimal("0"))
        },
        cors_origins="http://localhost:3000",
    )
