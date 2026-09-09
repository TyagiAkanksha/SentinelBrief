"""`persist_verdict`: verdict + tool_calls + status in ONE transaction (PRD §6.2, CONVENTIONS §3).

`flush()`-only, session-first, like every other service function — the caller (`TriagePipeline
.triage_alert`) owns the single commit that makes a verdict row, its tool-call trace, and the
alert's `triaged` status durable together, or rolls all three back together.

`TriageOutcome`/`ToolCallRecord` live in `worker/outcome.py` (m4 task-06, M2 final review M4-b):
this module imports them directly, no longer needing the pipeline module (that other worker
package that owns the triage prompt/LLM loop) at all, even under `TYPE_CHECKING` —
`ToolCallRecord` is re-exported here so `from worker.store import ToolCallRecord` (used
throughout the M0-M3 suite) keeps working.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ToolCallRow, VerdictRow
from core.services.alerts import set_alert_status
from worker.outcome import ToolCallRecord, TriageOutcome

__all__ = ["ToolCallRecord", "TriageOutcome", "persist_verdict"]


async def persist_verdict(
    session: AsyncSession,
    *,
    alert_id: uuid.UUID,
    outcome: TriageOutcome,
    model_primary: str,
    escalated_model: bool = False,
    tool_calls: Sequence[ToolCallRecord] = (),
) -> uuid.UUID:
    """Write `outcome`'s verdict row, its tool-call rows, and the alert's `triaged` status.

    No commit here — the caller owns exactly one transaction (PRD §6.2). Flushes only, so a
    constraint violation surfaces eagerly and the caller can roll the whole unit back.

    Args:
        session: The request/job-scoped `AsyncSession`; never committed here.
        alert_id: The alert the verdict belongs to.
        outcome: The pipeline's `TriageOutcome` (verdict, model, prompt version, usage, cost).
        model_primary: The model id the first-pass call used (may differ from `outcome.model`
            when routing escalated to a stronger model).
        escalated_model: Whether routing escalated to a stronger model for this verdict.
        tool_calls: The enrichment tool calls made during this run, in `seq` order.

    Returns:
        The new verdict row's id.
    """
    verdict = VerdictRow(
        alert_id=alert_id,
        severity=outcome.verdict.severity,
        category=outcome.verdict.category,
        confidence=outcome.verdict.confidence,
        reasoning=outcome.verdict.reasoning,
        recommended_action=outcome.verdict.recommended_action,
        escalate=outcome.verdict.escalate,
        model_primary=model_primary,
        model_final=outcome.model,
        escalated_model=escalated_model,
        prompt_version=outcome.prompt_version,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        cost_usd=outcome.cost_usd,
        latency_ms=outcome.latency_ms,
    )
    session.add(verdict)
    await session.flush()

    for record in tool_calls:
        session.add(
            ToolCallRow(
                verdict_id=verdict.id,
                seq=record.seq,
                tool_name=record.tool_name,
                arguments=record.arguments,
                result=record.result,
                latency_ms=record.latency_ms,
            )
        )

    await set_alert_status(session, alert_id, "triaged")
    await session.flush()
    return verdict.id
