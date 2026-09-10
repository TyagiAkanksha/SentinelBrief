"""Shared test helpers: fixture loading, signing, row counting, and DB seeding through
production writers (m3 task-01 — consolidates the M2 per-file private copies, plan defect 14).

Imported as `tests.helpers` (`tests/` has no `__init__.py`; `tests.fakes` already imports this
way). Seeded rows always go through `insert_alert` / `persist_verdict` / `set_alert_status`,
never a hand-rolled `session.add(...)`, so what a test asserts against is byte-for-byte what
ingest and triage actually produce.

m5 task-03 (the M4 task-01 "N5" carry-over) lifts `EchoTool`, `BoomTool`, `make_registry` and
`minimal_alert` here from their three separate per-file copies in `tests/test_tool_loop.py`,
`tests/test_tool_loop_db.py` and `tests/test_tool_registry.py` — a pure move, no behavior change
(PRD §6.3, §6.4).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from core.models import AlertRow, AlertStatus, Base, VerdictRow
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict
from core.services.alerts import insert_alert, set_alert_status
from core.signing import SIGNATURE_HEADER, sign_body
from worker.store import ToolCallRecord, persist_verdict
from worker.tools import LiveToolRecorder, ToolContext, ToolRegistry
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
    src_ip: str | None = None,
    received_at: datetime | None = None,
    verdict_created_at: datetime | None = None,
    cost_usd: Decimal = Decimal("0.000100"),
    latency_ms: int = 5,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> uuid.UUID:
    """Seed one alert, and optionally its verdict, entirely through production writers.

    `src_ip` overrides the envelope's top-level `src_ip` field (not any per-event `src_ip`) — it
    is what `AlertRow.raw["src_ip"].astext`/`ix_alerts_src_ip` reads, so tests that vary the
    source address for `get_alert_history` (m4 task-05) pass it here instead of hand-rolling a
    whole session payload.

    Returns the new alert's id; the caller commits — services and helpers never commit
    (CONVENTIONS.md §3).
    """
    load_overrides: dict[str, object] = {"session_id": session_id or uuid.uuid4().hex[:12]}
    if src_ip is not None:
        load_overrides["src_ip"] = src_ip
    alert = load_alert(name, **load_overrides)
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


# --- tool-loop test stubs, lifted from `tests/test_tool_loop.py` at m5 task-03 (M4 task-01 N5) ---

_TOOL_STUB_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)


class EchoTool:
    """Echoes its arguments back as the result — deterministic, no external seam.

    Byte-for-byte the copy previously duplicated in `tests/test_tool_loop.py`,
    `tests/test_tool_loop_db.py` (as `BoomTool`'s sibling, not itself) and
    `tests/test_tool_registry.py`; this module is the sole surviving definition site after the
    m5 task-03 lift.
    """

    name = "echo"
    description = "Echo the arguments back as the result."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return dict(arguments)


class BoomTool:
    """Violates the "tools never raise" contract on purpose — the registry's backstop must catch
    it (`ToolRegistry.execute`, controller ruling Q6), not the pipeline.

    Lifted at m5 task-03 (previously duplicated in `tests/test_tool_loop.py`,
    `tests/test_tool_loop_db.py`, `tests/test_tool_registry.py`).
    """

    name = "boom"
    description = "Always raises RuntimeError."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    external = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        raise RuntimeError("boom")


def make_registry(*tools: Any, max_result_chars: int = 4000) -> ToolRegistry:
    """A live-executed `ToolRegistry` over `tools`, at a given per-tool result budget.

    Lifted from `tests/test_tool_loop.py::_registry` at m5 task-03.
    """
    return ToolRegistry(list(tools), recorder=LiveToolRecorder(), max_result_chars=max_result_chars)


def minimal_alert() -> SessionAlert:
    """A minimal, valid two-event `SessionAlert` (connect + closed) — no fixture/DB needed.

    Lifted from `tests/test_tool_loop.py::_minimal_alert` at m5 task-03; distinct from
    `load_alert` above, which reads a real `fixtures/alerts/*.json` file.
    """
    events: list[dict[str, Any]] = [
        {
            "eventid": "cowrie.session.connect",
            "timestamp": _TOOL_STUB_BASE_TS.isoformat(),
            "session": "pipeline-test",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
        },
        {
            "eventid": "cowrie.session.closed",
            "timestamp": (_TOOL_STUB_BASE_TS + timedelta(seconds=5)).isoformat(),
            "session": "pipeline-test",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
            "duration_ms": 5000,
        },
    ]
    return SessionAlert.model_validate(
        {
            "source": "cowrie",
            "session_id": "pipeline-test",
            "src_ip": "203.0.113.9",
            "sensor": "hp-test-01",
            "events": events,
        }
    )
