"""Alembic environment: targets `core.models.Base.metadata`, honors the test-schema convention.

CONVENTIONS.md §6: Alembic is the only DDL path — no `create_all()` at startup. The database URL
comes from `alembic.ini`'s `sqlalchemy.url` FIRST (the test fixtures pin it explicitly via
`Config.set_main_option`, so a `DATABASE_URL` a developer happens to have exported can never
shadow the URL a caller pinned on purpose), falling back to `DATABASE_URL` then
`TEST_DATABASE_URL` env vars when the ini has none set. When `MIGRATE_SCHEMA` is set (the test
fixtures, CONVENTIONS.md §10), migrations run against that schema via `core.db.make_engine`'s
search_path mechanism, and Alembic's own `alembic_version` bookkeeping table is created in that
same schema too (`version_table_schema=schema`) so parallel/sequential throwaway schemas each get
independent migration state. Runs online migrations through the async engine
(`connection.run_sync`), driven by `asyncio.run` (CONVENTIONS.md §6).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context
from core.db import make_engine
from core.errors import ConfigError
from core.models import Base

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

    Raises:
        ConfigError: None of the three sources yields a non-empty URL (m5 task-05) — refusing
            here is clearer than handing SQLAlchemy an empty string and letting it raise its own
            `ArgumentError`/`NoSuchModuleError`.
    """
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DATABASE_URL")
        or ""
    )
    if not url:
        raise ConfigError("DATABASE_URL is not set")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL without a live DB connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=os.environ.get("MIGRATE_SCHEMA"),
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Configure the migration context against a sync `Connection` and run it.

    Called through `AsyncConnection.run_sync` from `run_async_migrations` below — Alembic's
    `context.run_migrations()` is itself sync.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=os.environ.get("MIGRATE_SCHEMA"),
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Build the async engine through `core.db.make_engine` and run migrations on it.

    `MIGRATE_SCHEMA` (set by the test fixtures) pins the connection's search_path exactly the
    way production code would, and pins Alembic's `alembic_version` table to that same schema
    via `version_table_schema`.
    """
    engine: AsyncEngine = make_engine(_database_url(), schema=os.environ.get("MIGRATE_SCHEMA"))

    async with engine.connect() as conn:
        await conn.run_sync(_run_migrations)

    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
