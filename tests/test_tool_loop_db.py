"""Pins the tool trace's persistence inside `TriagePipeline.triage_alert`'s ONE transaction
(PRD §6.2, §6.3) and the DB-touching `get_alert_history` tool running with the request's own
session (m4 task-06).

Every test here drives the pipeline through `persist_verdict`'s real write path (never a
hand-rolled `session.add(ToolCallRow(...))`), against a throwaway-schema Postgres database
(`tmp_schema`, CONVENTIONS.md §10). Documentation-range IPs only.

`BoomTool` moved to `tests/helpers.py` at m5 task-03 (a pure move, M4 task-01 N5 carry-over): it
was byte-for-byte duplicated here, in `tests/test_tool_loop.py` and in
`tests/test_tool_registry.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.models import AlertRow, ToolCallRow, VerdictRow
from tests.fakes import FakeLLMClient, ScriptedToolCall
from tests.helpers import BoomTool, load_alert, seed_alert
from worker.tools import AlertHistoryTool, LiveToolRecorder, ReplayToolRecorder, ToolRegistry
from worker.tools.wiring import build_registry
from worker.triage import TriagePipeline

_TOOL_FIXTURES_DIR = Path("tests/fixtures/tools")

# Severity-4 `successful_intrusion`, escalate=true — `tests/test_inline_triage.py`'s
# `_VALID_VERDICT_JSON`, copied per the brief (test files never import from each other).
VALID4 = (
    '{"severity": 4, "category": "successful_intrusion", "confidence": 0.9, '
    '"reasoning": "attacker logged in as root and ran reconnaissance commands", '
    '"recommended_action": "isolate host and rotate credentials", "escalate": true}'
)

# Severity-1 `scanning`, built through `Verdict` (per the brief) so it always validates.
_VALID1_VERDICT = {
    "severity": 1,
    "category": "scanning",
    "confidence": 0.6,
    "reasoning": "single connect and disconnect, no login attempt observed",
    "recommended_action": "no action; keep monitoring the source range",
    "escalate": False,
}
VALID1 = json.dumps(_VALID1_VERDICT)


async def _tool_call_rows(
    session_factory: async_sessionmaker[AsyncSession], alert_id: object
) -> list[ToolCallRow]:
    async with session_factory() as session:
        verdict = (
            await session.execute(select(VerdictRow).where(VerdictRow.alert_id == alert_id))
        ).scalar_one()
        rows = (
            (
                await session.execute(
                    select(ToolCallRow)
                    .where(ToolCallRow.verdict_id == verdict.id)
                    .order_by(ToolCallRow.seq)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def test_tool_calls_persisted_in_verdict_transaction(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    session_id = "toolloopdb-004"
    alert_id = await seed_alert(db_session, "alert4", session_id=session_id)
    await db_session.commit()

    fake = FakeLLMClient(
        [[ScriptedToolCall("get_session_commands", {"session_id": session_id})], VALID4]
    )
    registry = build_registry(Settings(), recorder=ReplayToolRecorder(_TOOL_FIXTURES_DIR))
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        tools=registry,
        tool_loop_max_iter=6,
    )

    status = await pipeline.triage_alert(db_session, alert_id)

    assert status == "triaged"

    rows = await _tool_call_rows(db_session_factory, alert_id)
    assert len(rows) == 1
    assert rows[0].tool_name == "get_session_commands"
    assert rows[0].seq == 0
    assert rows[0].result["commands"] == ["uname -a", "cat /etc/passwd", "w"]


async def test_loop_continues_and_persists_when_a_tool_raises(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert_id = await seed_alert(db_session, "alert4", session_id="toolloopdb-005")
    await db_session.commit()

    fake = FakeLLMClient([[ScriptedToolCall("boom", {"k": "v"})], VALID4])
    registry = ToolRegistry([BoomTool()], recorder=LiveToolRecorder(), max_result_chars=4000)
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        tools=registry,
        tool_loop_max_iter=6,
    )

    status = await pipeline.triage_alert(db_session, alert_id)

    assert status == "triaged"

    rows = await _tool_call_rows(db_session_factory, alert_id)
    assert len(rows) == 1
    assert rows[0].tool_name == "boom"
    assert rows[0].arguments == {"k": "v"}
    assert rows[0].result == {"unavailable": True, "reason": "RuntimeError: tool raised"}

    async with db_session_factory() as fresh:
        alert_row = await fresh.get(AlertRow, alert_id)
    assert alert_row is not None
    assert alert_row.status == "triaged"


async def test_validation_failure_after_tool_calls_writes_no_trace_and_marks_failed(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    session_id = "toolloopdb-006"
    alert_id = await seed_alert(db_session, "alert4", session_id=session_id)
    await db_session.commit()

    fake = FakeLLMClient(
        [[ScriptedToolCall("get_session_commands", {"session_id": session_id})], "bad", "bad"]
    )
    registry = build_registry(Settings(), recorder=ReplayToolRecorder(_TOOL_FIXTURES_DIR))
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        tools=registry,
        tool_loop_max_iter=6,
    )

    status = await pipeline.triage_alert(db_session, alert_id)

    assert status == "failed"

    async with db_session_factory() as fresh:
        verdict_count = await fresh.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == alert_id)
        )
        tool_call_count = await fresh.scalar(select(func.count()).select_from(ToolCallRow))
        alert_row = await fresh.get(AlertRow, alert_id)

    assert verdict_count == 0
    assert tool_call_count == 0
    assert alert_row is not None
    assert alert_row.status == "failed"


async def test_get_alert_history_runs_inside_triage_alert_with_the_request_session(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    shared_ip = "192.0.2.55"
    await seed_alert(db_session, "alert2", src_ip=shared_ip, session_id="hist-a")
    await seed_alert(db_session, "alert3", src_ip=shared_ip, session_id="hist-b")
    target_id = await seed_alert(db_session, "alert4", src_ip=shared_ip, session_id="hist-target")
    await db_session.commit()

    fake = FakeLLMClient(
        [[ScriptedToolCall("get_alert_history", {"ip": shared_ip, "window_hours": 24})], VALID4]
    )
    registry = ToolRegistry(
        [AlertHistoryTool(max_window_hours=720)],
        recorder=LiveToolRecorder(),
        max_result_chars=4000,
    )
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        tools=registry,
        tool_loop_max_iter=6,
    )

    status = await pipeline.triage_alert(db_session, target_id)

    assert status == "triaged"

    rows = await _tool_call_rows(db_session_factory, target_id)
    assert len(rows) == 1
    assert rows[0].result["count"] == 2


async def test_bare_port_scan_completes_with_at_most_one_tool_call() -> None:
    alert = load_alert("alert1")

    fake_with_tool = FakeLLMClient(
        [[ScriptedToolCall("get_ip_geo_asn", {"ip": alert.src_ip})], VALID1]
    )
    pipeline_with_tool = TriagePipeline(
        llm=fake_with_tool,
        model="fake-model",
        prompt_version="triage-v1",
        tools=build_registry(Settings(), recorder=ReplayToolRecorder(_TOOL_FIXTURES_DIR)),
        tool_loop_max_iter=6,
    )
    outcome_with_tool = await pipeline_with_tool.run(alert)
    assert len(outcome_with_tool.tool_calls) <= 1

    fake_no_tool = FakeLLMClient([VALID1])
    pipeline_no_tool = TriagePipeline(
        llm=fake_no_tool,
        model="fake-model",
        prompt_version="triage-v1",
        tools=build_registry(Settings(), recorder=ReplayToolRecorder(_TOOL_FIXTURES_DIR)),
        tool_loop_max_iter=6,
    )
    outcome_no_tool = await pipeline_no_tool.run(alert)
    assert len(outcome_no_tool.tool_calls) == 0
