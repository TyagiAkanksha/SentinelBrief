"""Two-tier routing decision: whether a cheap-tier verdict escalates to the strong model.

PRD §6.4: every alert goes to the cheap model first; the SAME context escalates to the strong
model iff `severity >= ESCALATE_SEVERITY_GTE` OR `confidence < ESCALATE_CONFIDENCE_LT` (settings,
never a literal outside this pure function and its tests — CONVENTIONS.md §7). Severity wins the
`reason` when both conditions hold, deterministically, so callers never have to guess which
threshold actually fired.

`should_escalate` is pure (no I/O, no settings read) so `worker/triage.py::TriagePipeline.run`
stays the only place a routing decision has side effects (the strong-model call itself).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.schemas.verdict import Verdict

EscalationReason = Literal["severity", "confidence", "none"]


@dataclass(frozen=True)
class RoutingDecision:
    """The outcome of `should_escalate`: whether to escalate, and why."""

    escalate: bool
    reason: EscalationReason


def should_escalate(
    verdict: Verdict, *, severity_gte: int, confidence_lt: float
) -> RoutingDecision:
    """Decide whether `verdict` (the cheap tier's) should escalate to the strong model.

    Severity is checked first: `verdict.severity >= severity_gte` wins the reason even when
    `verdict.confidence < confidence_lt` also holds (PRD §6.4, pinned by
    `tests/test_routing.py::test_severity_reason_takes_precedence_over_confidence`). The
    confidence check is strict `<` — confidence exactly equal to the threshold never escalates.

    Args:
        verdict: The cheap tier's validated verdict.
        severity_gte: Escalate when `verdict.severity` is at least this (`Settings
            .escalate_severity_gte`, never a literal at the call site).
        confidence_lt: Escalate when `verdict.confidence` is strictly below this (`Settings
            .escalate_confidence_lt`, never a literal at the call site).

    Returns:
        `RoutingDecision(escalate=True, reason="severity")` when the severity threshold fires;
        `RoutingDecision(escalate=True, reason="confidence")` when only the confidence threshold
        fires; `RoutingDecision(escalate=False, reason="none")` otherwise.
    """
    if verdict.severity >= severity_gte:
        return RoutingDecision(escalate=True, reason="severity")
    if verdict.confidence < confidence_lt:
        return RoutingDecision(escalate=True, reason="confidence")
    return RoutingDecision(escalate=False, reason="none")
