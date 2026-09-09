"""Shared test helpers: fixture loading, signing, row counting, and DB seeding through
production writers (m3 task-01 — consolidates the M2 per-file private copies, plan defect 14).

Imported as `tests.helpers` (`tests/` has no `__init__.py`; `tests.fakes` already imports this
way). Seeded rows always go through `insert_alert` / `persist_verdict` / `set_alert_status`,
never a hand-rolled `session.add(...)`, so what a test asserts against is byte-for-byte what
ingest and triage actually produce.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from core.models import AlertRow, AlertStatus, Base, VerdictRow
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict
from core.services.alerts import insert_alert, set_alert_status
from core.signing import SIGNATURE_HEADER, sign_body
from worker.store import ToolCallRecord, persist_verdict
from worker.triage import TriageOutcome

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "alerts"
TEST_SECRET = "test-secret"  # matches conftest's `settings` fixture's `ingest_hmac_secret`


def fixture_body(name: str = "alert4") -> bytes:
    """Return the raw bytes of `fixtures/alerts/<name>.json`."""
    return (FIXTURES_DIR / f"{name}.json").read_bytes()


def load_alert(name: str = "alert4", **overrides: object) -> SessionAlert:
    """Load `fixtures/alerts/<name>.json`, applying top-level field overrides.

    A distinct `session_id` per call mints a fresh fingerprint without hand-rolling a whole
    session payload (`SessionAlert.fingerprint()` is `sha256(source|session_id|connect_time)`).
    """
    data = json.loads(fixture_body(name))
    data.update(overrides)
    return SessionAlert.model_validate(data)


def signed_headers(secret: str, body: bytes) -> dict[str, str]:
    """A signed `X-Signature` + `content-type` header pair for `body` under `secret`."""
    return {SIGNATURE_HEADER: sign_body(secret, body), "content-type": "application/json"}


async def count_rows(session: AsyncSession, model: type[Base]) -> int:
    """Count `model`'s rows visible in `session`."""
    count = await session.scalar(select(func.count()).select_from(model))
    assert count is not None
    return count


async def count_rows_fresh(
    session_factory: async_sessionmaker[AsyncSession], model: type[Base]
) -> int:
    """Count `model`'s rows from a fresh session — visibility from a fresh connection."""
    async with session_factory() as session:
        return await count_rows(session, model)


async def add_verdict(
    session: AsyncSession,
    alert_id: uuid.UUID,
    verdict: Verdict,
    *,
    created_at: datetime | None = None,
    cost_usd: Decimal = Decimal("0.000100"),
    latency_ms: int = 5,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> uuid.UUID:
    """Persist `verdict` for `alert_id` through the production writer (`persist_verdict`).

    When `created_at` is given, the row's `created_at` is set and flushed afterward, so two
    verdicts written in the same transaction (which would otherwise share `now()`) can be made
    to sort deterministically in ordering tests.
    """
    outcome = TriageOutcome(
        verdict=verdict,
        model="fake-model",
        prompt_version="triage-v1",
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        retried=False,
    )
    verdict_id = await persist_verdict(
        session,
        alert_id=alert_id,
        outcome=outcome,
        model_primary="fake-model",
        tool_calls=tool_calls,
    )
    if created_at is not None:
        row = await session.get(VerdictRow, verdict_id)
        assert row is not None
        row.created_at = created_at
        await session.flush()
    return verdict_id


async def seed_alert(
    session: AsyncSession,
    name: str = "alert4",
    *,
    verdict: Verdict | None = None,
    status: AlertStatus | None = None,
    session_id: str | None = None,
    received_at: datetime | None = None,
    verdict_created_at: datetime | None = None,
    cost_usd: Decimal = Decimal("0.000100"),
    latency_ms: int = 5,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> uuid.UUID:
    """Seed one alert, and optionally its verdict, entirely through production writers.

    Returns the new alert's id; the caller commits — services and helpers never commit
    (CONVENTIONS.md §3).
    """
    alert = load_alert(name, session_id=session_id or uuid.uuid4().hex[:12])
    result = await insert_alert(session, alert)
    assert result.created, "seed_alert: duplicate fingerprint is a test bug"

    if received_at is not None:
        row = await session.get(AlertRow, result.alert_id)
        assert row is not None
        row.received_at = received_at
        await session.flush()

    if verdict is not None:
        await add_verdict(
            session,
            result.alert_id,
            verdict,
            created_at=verdict_created_at,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
        )

    effective_status: AlertStatus = (
        status if status is not None else ("triaged" if verdict is not None else "pending")
    )
    row = await session.get(AlertRow, result.alert_id)
    assert row is not None
    if row.status != effective_status:
        await set_alert_status(session, result.alert_id, effective_status)

    await session.flush()
    return result.alert_id
