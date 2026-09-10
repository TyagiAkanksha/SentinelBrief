"""Pins `worker.routing.should_escalate` and the `Settings` fields that drive it (m5 task-03).

PRD §6.4 (escalate to the strong model iff the cheap verdict's `severity >= ESCALATE_SEVERITY_GTE`
or `confidence < ESCALATE_CONFIDENCE_LT` — severity wins the reason when both hold); CONVENTIONS.md
§7 (routing thresholds are `Settings`, never a literal, except in this deliberately tautological
defaults test — R17: literal + comment). `should_escalate` is pure (no I/O): every case here is a
plain function call, no fake, no DB.

`worker.routing` does not exist yet, so every test in this module is RED at collection with
`ModuleNotFoundError: No module named 'worker.routing'`, not merely at first use.
"""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from core.config import Settings
from core.schemas.verdict import Verdict
from worker.routing import RoutingDecision, should_escalate


def _verdict(severity: int, confidence: float) -> Verdict:
    """A minimally-valid `Verdict` at the given severity/confidence; only those two fields drive
    `should_escalate` — category/reasoning/recommended_action are fixed filler, and `escalate`
    follows the PRD §6.6 rubric (severity >= 4 requires it) purely so the `Verdict` itself
    validates, independent of the routing decision under test."""
    return Verdict(
        severity=severity,
        category="brute_force",
        confidence=confidence,
        reasoning="synthetic test reasoning citing session evidence.",
        recommended_action="synthetic recommended action.",
        escalate=severity >= 4,
    )


# (severity, confidence, severity_gte, confidence_lt, expected_escalate, expected_reason)
_WORKED_TABLE = [
    # --- at the PRD §6.4 defaults (4, 0.6) ---
    (4, 0.9, 4, 0.6, True, "severity"),
    (3, 0.59, 4, 0.6, True, "confidence"),
    (3, 0.6, 4, 0.6, False, "none"),  # strict < : confidence == threshold never escalates
    (5, 0.1, 4, 0.6, True, "severity"),
    (1, 0.6, 4, 0.6, False, "none"),
    (4, 0.1, 4, 0.6, True, "severity"),
    (3, 0.0, 4, 0.6, True, "confidence"),
    (3, 1.0, 4, 0.6, False, "none"),
    # --- non-default severity_gte=5 (confidence_lt stays 4, 0.6's own default: 0.6) ---
    (4, 0.9, 5, 0.6, False, "none"),
    (5, 0.9, 5, 0.6, True, "severity"),
    # --- non-default confidence_lt=0.0 (severity_gte stays the default: 4) ---
    (3, 0.0, 4, 0.0, False, "none"),
]


@pytest.mark.parametrize(
    (
        "severity",
        "confidence",
        "severity_gte",
        "confidence_lt",
        "expected_escalate",
        "expected_reason",
    ),
    _WORKED_TABLE,
)
def test_should_escalate_worked_table(
    severity: int,
    confidence: float,
    severity_gte: int,
    confidence_lt: float,
    expected_escalate: bool,
    expected_reason: str,
) -> None:
    decision = should_escalate(
        _verdict(severity, confidence), severity_gte=severity_gte, confidence_lt=confidence_lt
    )

    assert decision.escalate is expected_escalate
    assert decision.reason == expected_reason


def test_severity_reason_takes_precedence_over_confidence() -> None:
    """`(severity=5, confidence=0.1)` at the defaults trips BOTH conditions; the reason must be
    `"severity"`, deterministically — never `"confidence"` (PRD §6.4, pinned in the brief's own
    Interfaces block)."""
    decision = should_escalate(_verdict(5, 0.1), severity_gte=4, confidence_lt=0.6)

    assert decision.escalate is True
    assert decision.reason == "severity"


def test_routing_decision_is_frozen() -> None:
    decision = RoutingDecision(escalate=True, reason="severity")

    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.escalate = False  # type: ignore[misc]


def test_routing_settings_defaults_and_bounds() -> None:
    """R17: literal defaults, not read back from `Settings()` itself — the whole point of this
    test is to catch an accidental default change, so comparing `Settings().x` against itself
    would prove nothing.
    """
    settings = Settings()

    assert settings.escalate_severity_gte == 4  # PRD §6.4 default (ESCALATE_SEVERITY_GTE)
    assert settings.escalate_confidence_lt == 0.6  # PRD §6.4 default (ESCALATE_CONFIDENCE_LT)

    with pytest.raises(ValidationError):
        Settings(escalate_severity_gte=0)
    with pytest.raises(ValidationError):
        Settings(escalate_severity_gte=6)
    with pytest.raises(ValidationError):
        Settings(escalate_confidence_lt=1.5)
