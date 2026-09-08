"""Pins `worker/store.py::persist_verdict`: verdict + tool_calls + `alerts.status` written as
**one** unit that the caller commits exactly once (PRD §6.2, CONVENTIONS.md §3) — m2 task-04.

`test_persist_verdict_is_all_or_nothing` is the load-bearing test: it commits the alert's own
insert first (a real caller — `TriagePipeline.triage_alert` — always loads an already-committed
alert), then proves that a NOT-NULL violation inside the verdict/tool_calls write rolls back
*only* that write, leaving the alert row exactly as it was (`pending`) and no partial verdict
row behind. The other three tests share a plain `insert_alert` + `flush` setup (no commit needed
since none of them roll back).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.models import AlertRow, ToolCallRow, VerdictRow
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict
from core.services.alerts import insert_alert
from worker.store import ToolCallRecord, persist_verdict
from worker.triage import TriageOutcome

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "alerts" / "alert4.json"


async def _insert_alert(session: AsyncSession, *, session_id: str) -> uuid.UUID:
    """Insert a fresh `AlertRow` (distinct `session_id` -> distinct fingerprint), flush only."""
    data = json.loads(_FIXTURE.read_text())
    data["session_id"] = session_id
    alert = SessionAlert.model_validate(data)
    result = await insert_alert(session, alert)
    await session.flush()
    return result.alert_id


def _make_outcome(**overrides: Any) -> TriageOutcome:
    """A valid `TriageOutcome` built by hand, so `persist_verdict` is pinned in isolation from
    `TriagePipeline.run`."""
    verdict = Verdict(
        severity=3,
        category="scanning",
        confidence=0.7,
        reasoning="automated scan pattern",
        recommended_action="monitor",
        escalate=False,
    )
    defaults: dict[str, Any] = {
        "verdict": verdict,
        "model": "fake-model",
        "prompt_version": "triage-v1",
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": Decimal("0.000100"),
        "latency_ms": 5,
        "retried": False,
    }
    defaults.update(overrides)
    return TriageOutcome(**defaults)


async def test_persist_verdict_writes_row_and_sets_triaged(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert_id = await _insert_alert(db_session, session_id="store-001")
    outcome = _make_outcome()

    # (a) a commit spy: `persist_verdict` must never call `session.commit()` itself — the caller
    # owns exactly one transaction (PRD §6.2). `db_session.in_transaction()` cannot pin this: it
    # would report True regardless, since the `.get()` reads below autobegin a fresh transaction
    # after any commit.
    commit_calls: list[int] = []

    async def _spy_commit() -> None:
        commit_calls.append(1)

    monkeypatch.setattr(db_session, "commit", _spy_commit)

    verdict_id = await persist_verdict(
        db_session, alert_id=alert_id, outcome=outcome, model_primary="fake-model"
    )

    assert commit_calls == []

    # (b) a second, independent session/connection must not see the verdict row yet — proves the
    # write is genuinely still uncommitted, not just that our own session hasn't called commit().
    async with db_session_factory() as other_session:
        other_count = await other_session.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
    assert other_count == 0

    assert isinstance(verdict_id, uuid.UUID)

    row = await db_session.get(VerdictRow, verdict_id)
    assert row is not None
    assert row.alert_id == alert_id
    assert row.severity == outcome.verdict.severity
    assert row.category == outcome.verdict.category
    assert row.confidence == pytest.approx(outcome.verdict.confidence)
    assert row.reasoning == outcome.verdict.reasoning
    assert row.recommended_action == outcome.verdict.recommended_action
    assert row.escalate == outcome.verdict.escalate
    assert row.model_primary == "fake-model"
    assert row.model_final == outcome.model
    assert row.escalated_model is False
    assert row.prompt_version == outcome.prompt_version
    assert row.input_tokens == outcome.input_tokens
    assert row.output_tokens == outcome.output_tokens
    assert row.cost_usd == outcome.cost_usd
    assert row.latency_ms == outcome.latency_ms

    alert_row = await db_session.get(AlertRow, alert_id)
    assert alert_row is not None
    assert alert_row.status == "triaged"


async def test_persist_verdict_writes_tool_calls_in_seq_order(db_session: AsyncSession) -> None:
    alert_id = await _insert_alert(db_session, session_id="store-002")
    outcome = _make_outcome()
    tool_calls = (
        ToolCallRecord(
            seq=0,
            tool_name="check_reputation",
            arguments={"ip": "192.0.2.55"},
            result={"score": 42},
            latency_ms=12,
        ),
        ToolCallRecord(
            seq=1,
            tool_name="get_session_commands",
            arguments={"session_id": "4d5e6f708192"},
            result={"commands": ["uname -a"]},
            latency_ms=8,
        ),
    )

    verdict_id = await persist_verdict(
        db_session,
        alert_id=alert_id,
        outcome=outcome,
        model_primary="fake-model",
        tool_calls=tool_calls,
    )

    rows = (
        (
            await db_session.execute(
                select(ToolCallRow)
                .where(ToolCallRow.verdict_id == verdict_id)
                .order_by(ToolCallRow.seq)
            )
        )
        .scalars()
        .all()
    )

    assert [r.seq for r in rows] == [0, 1]
    assert [r.tool_name for r in rows] == ["check_reputation", "get_session_commands"]
    assert rows[0].arguments == {"ip": "192.0.2.55"}
    assert rows[0].result == {"score": 42}
    assert rows[0].latency_ms == 12
    assert rows[1].arguments == {"session_id": "4d5e6f708192"}
    assert rows[1].result == {"commands": ["uname -a"]}
    assert rows[1].latency_ms == 8


async def test_persist_verdict_is_all_or_nothing(db_session: AsyncSession) -> None:
    alert_id = await _insert_alert(db_session, session_id="store-003")
    # Commit the alert's own insert first: a real caller always loads an already-committed
    # alert, so the rollback below must undo only persist_verdict's own write, not this row.
    await db_session.commit()

    outcome = _make_outcome()
    bad_tool_call = ToolCallRecord(
        seq=0,
        tool_name=None,  # type: ignore[arg-type]
        arguments={},
        result={},
        latency_ms=1,
    )

    with pytest.raises(IntegrityError):
        await persist_verdict(
            db_session,
            alert_id=alert_id,
            outcome=outcome,
            model_primary="fake-model",
            tool_calls=(bad_tool_call,),
        )

    await db_session.rollback()

    verdict_count = await db_session.scalar(
        select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
    )
    assert verdict_count == 0

    alert_row = await db_session.get(AlertRow, alert_id)
    assert alert_row is not None
    assert alert_row.status == "pending"


async def test_persist_verdict_escalated_model_flag(db_session: AsyncSession) -> None:
    alert_id = await _insert_alert(db_session, session_id="store-004")
    outcome = _make_outcome(model="strong-model")

    verdict_id = await persist_verdict(
        db_session,
        alert_id=alert_id,
        outcome=outcome,
        model_primary="fake-model",
        escalated_model=True,
    )

    row = await db_session.get(VerdictRow, verdict_id)
    assert row is not None
    assert row.model_primary == "fake-model"
    assert row.model_final == "strong-model"
    assert row.escalated_model is True
