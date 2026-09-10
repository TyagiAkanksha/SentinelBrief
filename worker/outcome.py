"""`TriageOutcome` and `ToolCallRecord`: the pipeline's result types (PRD §6.2, §6.3).

Relocated from `worker/triage.py` (`TriageOutcome`) and `worker/store.py` (`ToolCallRecord`, m4
task-06, M2 final review M4-b) so `worker/store.py::persist_verdict` (which takes an
`outcome: TriageOutcome` parameter and writes `ToolCallRecord`s) no longer needs a
`TYPE_CHECKING`-only import of `worker.triage` to type itself — this module imports nothing from
either, so both can import it directly with no cycle. `worker/triage.py` re-exports both names so
`from worker.triage import TriageOutcome` (used throughout the M0-M3 suite) keeps working.

Both are `frozen=True`: a triage result and its tool-call trace are immutable facts about one
completed run, never mutated after construction.

`TriageOutcome` gains `model_primary`/`escalated_model` at m5 task-03 (PRD §6.4, two-tier
routing): both default so every pre-task-03 construction keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.schemas.verdict import Verdict


@dataclass(frozen=True)
class ToolCallRecord:
    """One recorded tool invocation to persist alongside its verdict (PRD §6.3)."""

    seq: int
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    latency_ms: int


@dataclass(frozen=True)
class TriageOutcome:
    """One triage pipeline run's verdict plus billing/routing/tool-trace metadata.

    `tool_calls` defaults to `()` so every pre-task-06 `TriageOutcome(...)` construction across
    the M0-M3 suite keeps working unchanged (PRD §6.3, m4 task-06).
    """

    verdict: Verdict
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    retried: bool
    tool_calls: tuple[ToolCallRecord, ...] = ()
    model_primary: str | None = None
    """The cheap-tier model id, when routing escalated; `None` means no routing happened (`model`
    is already the only model this run used) — `persist_verdict` receives `model_primary or
    model` (m5 task-03, PRD §6.4)."""
    escalated_model: bool = False
    """Whether routing escalated this run to the strong model (m5 task-03, PRD §6.4)."""
