"""Async engine and session-factory construction on psycopg 3 (CONVENTIONS.md §6).

`alembic/env.py` and the DB test fixtures (`tests/conftest.py`) build their engines through this
module rather than calling `create_async_engine` directly — one code path for how SentinelBrief
talks to Postgres.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(database_url: str, *, schema: str | None = None) -> AsyncEngine:
    """Build an async engine, optionally pinned to a schema via search_path.

    A bare `postgresql://` URL is normalized to the `postgresql+psycopg` dialect so the engine
    always talks to the database over psycopg (v3).

    When `schema` is given, the engine's libpq startup options set
    `search_path=<schema>,public`. The `,public` fallback lets shared, installed-once
    infrastructure that only ever lives in `public` still resolve when connected with a
    non-public search_path.

    `hide_parameters=True`: a `DBAPIError`'s `str()` never renders bound parameters —
    `alerts.raw` and verdict text are attacker-derived (PRD §10.6) and both `api/errors.py`'s 500
    handler and ARQ's failure log render `str(exc)`; m5 final review, ledger t02-hide.

    Args:
        database_url: a `postgresql://` (or already-qualified `postgresql+psycopg://`) URL.
        schema: when set, the schema to prefer on the connection search_path.

    Returns:
        A configured `AsyncEngine`. Callers own disposal.
    """
    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")

    connect_args: dict[str, str] = {}
    if schema is not None:
        connect_args["options"] = f"-csearch_path={schema},public"

    return create_async_engine(url, connect_args=connect_args, hide_parameters=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to `engine`.

    `expire_on_commit=False` so ORM instances stay usable (e.g. for response serialization)
    after the caller commits the transaction.

    Args:
        engine: the engine to bind sessions to.

    Returns:
        An `async_sessionmaker` producing `AsyncSession` objects bound to `engine`.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
