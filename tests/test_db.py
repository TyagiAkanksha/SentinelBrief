"""Pins `core.db.make_engine` (CONVENTIONS.md §6): driver normalization and the `search_path`
wiring every DB-fixture test in the suite depends on (m2 task-01). No DB connection is made —
`create_async_engine` builds an engine object lazily, so these run with `TEST_DATABASE_URL`
unset too.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from core.db import make_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _pool_creator_connect_kwargs(engine: AsyncEngine) -> Mapping[str, object]:
    """Extract the DBAPI connect kwargs SQLAlchemy will use to open `engine`'s connections.

    No public SQLAlchemy API exposes the `connect_args` passed to `create_async_engine` once the
    engine is built: `dialect.create_connect_args(url)` recomputes args from the URL alone (not
    from the separate `connect_args=` kwarg `core.db.make_engine` is specified to pass), and the
    `do_connect` event only fires on a real connection attempt, which a DB-less test must not
    make. Instead this reaches into `Pool._creator` — the connection-opening closure
    `sqlalchemy.engine.create_engine` builds internally — and reads its `cparams` free variable,
    which holds the final DBAPI kwargs (including `options`). Verified against SQLAlchemy 2.0.52;
    if a future SQLAlchemy release changes this closure shape, the fix is either a version pin or
    replacing this test with a `do_connect`-event test against a live DB.
    """
    creator = engine.sync_engine.pool._creator  # type: ignore[attr-defined]
    freevars = creator.__code__.co_freevars
    cell_values = (cell.cell_contents for cell in (creator.__closure__ or ()))
    closed_over = dict(zip(freevars, cell_values, strict=True))
    cparams = closed_over.get("cparams")
    assert isinstance(cparams, Mapping), (
        f"expected a 'cparams' mapping in {engine!r}'s pool creator closure, "
        f"found free variables {sorted(freevars)}"
    )
    return cparams


def test_make_engine_normalizes_driver() -> None:
    engine = make_engine("postgresql://u:p@h/d")
    assert engine.url.drivername == "postgresql+psycopg"


def test_search_path_option_set() -> None:
    engine = make_engine("postgresql://u:p@h/d", schema="s")

    # The search_path travels through `connect_args`, never through the URL's query string.
    assert dict(engine.url.query) == {}

    connect_kwargs = _pool_creator_connect_kwargs(engine)
    assert connect_kwargs.get("options") == "-csearch_path=s,public"
