"""Alembic environment: targets `core.models.Base.metadata`, honors the test-schema convention.

CONVENTIONS.md §6: Alembic is the only DDL path — no `create_all()` at startup. The database URL
comes from `alembic.ini`'s `sqlalchemy.url` FIRST (the test fixtures pin it explicitly via
`Config.set_main_option`, so a `DATABASE_URL` a developer happens to have exported can never
shadow the URL a caller pinned on purpose), falling back to `DATABASE_URL` then
`TEST_DATABASE_URL` env vars when the ini has none set. When `MIGRATE_SCHEMA` is set (the test
fixtures, CONVENTIONS.md §10), migrations run against that schema via `core.db.make_engine`'s
search_path mechanism (`<schema>,public`). Alembic's own `alembic_version` bookkeeping table is
deliberately kept OUT of both of those schemas (see `_version_table_kwargs`):
`tests/test_models_schema.py::test_migration_creates_all_four_tables` asserts a migrated
throwaway schema contains **exactly** the four PRD §5 tables, and
`tests/test_alembic_roundtrip.py::test_compare_metadata_empty` reflects every table *visible*
on the connection's search_path (which always includes `public`) — so bookkeeping lives in a
third, dedicated schema (`_BOOKKEEPING_SCHEMA`) outside the search_path entirely, under a name
unique to that throwaway schema (never a single shared name — a shared table would let one test
schema's recorded "at head" state make a later, freshly-created schema's migration a silent
no-op). Runs online migrations through the async engine (`connection.run_sync`), driven by
`asyncio.run` (CONVENTIONS.md §6).
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from logging.config import fileConfig

from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context
from core.db import make_engine
from core.models import Base

# Alembic's own bookkeeping table lives here when `MIGRATE_SCHEMA` is set — never inside the
# throwaway schema itself, and never in `public` either (both are on the connection's
# search_path, which the roundtrip/schema-parity tests reflect in full). Created on demand in
# `_run_migrations`; never dropped — see the module docstring's dev/test-only note.
_BOOKKEEPING_SCHEMA = "sentinelbrief_alembic"

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    # `disable_existing_loggers=False` — the stdlib default (`True`) silently disables every
    # logger that existed before this call, including any a caller (e.g. `pytest`) already
    # configured. Alembic only owns its own root/sqlalchemy/alembic loggers here.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# `core.models` (imported above) registers every PRD §5 table on this metadata.
target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the database URL: `alembic.ini`'s `sqlalchemy.url` FIRST, then env vars.

    The ini value wins over `DATABASE_URL`/`TEST_DATABASE_URL` so a caller that pins the URL
    explicitly (the test fixtures, via `Config.set_main_option`) can never be silently
    overridden by whatever a developer happens to have exported in their shell.
    """
    return (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DATABASE_URL")
        or ""
    )


def _version_table_kwargs(schema: str | None) -> dict[str, str]:
    """`context.configure` kwargs pinning Alembic's bookkeeping table's location.

    Outside a throwaway test schema (`schema is None`, production/dev), Alembic's defaults
    apply — no kwargs, and the version table lands wherever the connection's own default schema
    resolves (`public` in practice). When `MIGRATE_SCHEMA` is set, the version table is pinned
    to `_BOOKKEEPING_SCHEMA` under a name unique to that schema (see the module docstring).
    """
    if schema is None:
        return {}
    return {
        "version_table": f"alembic_version_{schema}",
        "version_table_schema": _BOOKKEEPING_SCHEMA,
    }


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL without a live DB connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_version_table_kwargs(os.environ.get("MIGRATE_SCHEMA")),
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Configure the migration context against a sync `Connection` and run it.

    Called through `AsyncConnection.run_sync` from `run_async_migrations` below — Alembic's
    `context.run_migrations()` is itself sync.
    """
    schema = os.environ.get("MIGRATE_SCHEMA")
    if schema is not None:
        # `_BOOKKEEPING_SCHEMA` is outside every throwaway schema's search_path on purpose (see
        # the module docstring), so nothing else ever creates it — do so here, idempotently,
        # before Alembic's own version-table DDL needs it to exist.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_BOOKKEEPING_SCHEMA}"'))
        connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **_version_table_kwargs(schema),
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Build the async engine through `core.db.make_engine` and run migrations on it.

    `MIGRATE_SCHEMA` (set by the test fixtures) pins the connection's search_path exactly the
    way production code would.
    """
    engine: AsyncEngine = make_engine(_database_url(), schema=os.environ.get("MIGRATE_SCHEMA"))

    async with engine.connect() as conn:
        await conn.run_sync(_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection.

    Driven by `asyncio.run`. `tests/test_alembic_roundtrip.py::test_upgrade_downgrade_upgrade`
    calls the sync `alembic.command.upgrade`/`downgrade` helpers directly from inside an `async
    def` test — i.e. from a thread that already has a running event loop (pytest-asyncio's), in
    which `asyncio.run` cannot nest. When that is detected, the migration runs on a dedicated
    thread with its own fresh loop instead; the throwaway-schema fixture (`tmp_schema`, sync on
    purpose per CONVENTIONS.md §10) never hits this branch.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async_migrations())
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: asyncio.run(run_async_migrations())).result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
