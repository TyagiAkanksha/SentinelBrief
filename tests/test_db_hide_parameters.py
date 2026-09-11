"""New file (m5 fix wave, ledger t02-hide / verdict block C1): pins `core/db.py::make_engine`'s
`hide_parameters=True`.

`alerts.raw` and verdict text are attacker-derived (PRD §10.6); both `api/errors.py`'s 500 handler
and ARQ's own failure log render `str(exc)` on an unhandled exception. Without
`hide_parameters=True` a `sqlalchemy.exc.DBAPIError`'s `str()` renders the bound parameter values
verbatim — proven reachable by the m5 final review's probe P1. RED at HEAD (`core/db.py:45` has no
`hide_parameters` kwarg yet): the marker survives into `str(exc)`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def test_dbapi_errors_never_render_bound_parameters(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """core/db.py::make_engine sets hide_parameters=True (m5 fix wave, ledger t02-hide)."""
    marker = "ATTACKER-PAYLOAD-MARKER-9c1e"
    async with db_session_factory() as session:
        with pytest.raises(DBAPIError) as info:
            await session.execute(
                text(
                    "INSERT INTO alerts (fingerprint, source, event_time, raw) "
                    "VALUES (:fp, :src, :t, :raw)"
                ),
                {
                    "fp": "hide-params-fp",
                    "src": "cowrie",
                    "t": "not-a-timestamp",
                    "raw": '{"banner": "' + marker + '"}',
                },
            )
    assert marker not in str(info.value)
    assert "hide_parameters=True" in str(info.value)
