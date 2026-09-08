"""Pins Alembic migration `0001_initial_schema`'s round-trip and its parity with the ORM
metadata (PRD §5; CONVENTIONS.md §6): `upgrade head` -> `downgrade base` -> `upgrade head` all
succeed and leave the schema exactly matching `core.models.Base.metadata` (m2 task-01).

Uses `db_engine`/`tmp_schema` (CONVENTIONS.md §10) — real Postgres, skipped by fixture name when
`TEST_DATABASE_URL` is unset.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

from core.models import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    # See tests/conftest.py::tmp_schema — configparser treats a bare "%" as an interpolation
    # token; doubling it is configparser's own escape for a percent-encoded URL password.
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def _ignore_alembic_version_table(
    object_: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """`compare_metadata`'s `include_object` filter: exclude Alembic's own bookkeeping table.

    `alembic_version` is created by Alembic itself and has no ORM model — without this filter
    `compare_metadata` always reports it as a spurious `remove_table` diff.
    """
    return not (type_ == "table" and name == "alembic_version")


async def test_upgrade_downgrade_upgrade(
    db_engine: AsyncEngine, tmp_schema: tuple[str, str]
) -> None:
    """`tmp_schema` already ran `upgrade head`; downgrading to `base` must drop every §5 table,
    and upgrading to `head` again must recreate all four cleanly."""
    database_url, schema = tmp_schema
    cfg = _alembic_config(database_url)

    previous_migrate_schema = os.environ.get("MIGRATE_SCHEMA")
    os.environ["MIGRATE_SCHEMA"] = schema
    try:
        # Sync Alembic commands run in a worker thread, never awaited directly: this test is
        # `async def` (it needs `db_engine` for reflection), but `alembic/env.py` drives its own
        # migrations with a plain `asyncio.run(...)`, which cannot nest inside this coroutine's
        # already-running event loop (CONVENTIONS.md §10).
        await asyncio.to_thread(downgrade, cfg, "base")

        async with db_engine.connect() as conn:
            tables_after_downgrade = await conn.run_sync(
                lambda sync_conn: set(sa.inspect(sync_conn).get_table_names(schema=schema))
            )
        # `downgrade base` clears alembic_version's row but does not drop the table itself — it
        # persists in this schema by design (`version_table_schema=schema`); exclude it here too.
        assert tables_after_downgrade - {"alembic_version"} == set()

        await asyncio.to_thread(upgrade, cfg, "head")

        async with db_engine.connect() as conn:
            tables_after_reupgrade = await conn.run_sync(
                lambda sync_conn: set(sa.inspect(sync_conn).get_table_names(schema=schema))
            )
        # Same bookkeeping-table exclusion as above — alembic_version was never dropped, so it
        # is still present after the re-upgrade too.
        assert tables_after_reupgrade - {"alembic_version"} == {
            "alerts",
            "verdicts",
            "tool_calls",
            "eval_runs",
        }
    finally:
        if previous_migrate_schema is None:
            os.environ.pop("MIGRATE_SCHEMA", None)
        else:
            os.environ["MIGRATE_SCHEMA"] = previous_migrate_schema


async def test_compare_metadata_empty(db_engine: AsyncEngine) -> None:
    """`compare_metadata` over the head-migrated schema against `Base.metadata` is `[]` — the
    migration and the ORM models never drift (m2 task-01 acceptance)."""

    def _diff(sync_conn: sa.Connection) -> list[Any]:
        migration_context = MigrationContext.configure(
            sync_conn,
            opts={"include_object": _ignore_alembic_version_table},
        )
        return list(compare_metadata(migration_context, Base.metadata))

    async with db_engine.connect() as conn:
        diff = await conn.run_sync(_diff)

    assert diff == []
