"""Pins core.schemas.verdict.Verdict: PRD §6.5 bounds and the §6.6 escalate rule (m0 task-02)."""

from __future__ import annotations

import pytest
from core.schemas.verdict import Verdict
from pydantic import ValidationError


def _verdict_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "severity": 3,
        "category": "scanning",
        "confidence": 0.72,
        "reasoning": "Automated scanning behavior consistent with mass internet scanners.",
        "recommended_action": "No action needed; monitor for escalation.",
        "escalate": False,
    }
    base.update(overrides)
    return base


def test_valid_verdict_parses() -> None:
    verdict = Verdict(**_verdict_kwargs())

    assert verdict.severity == 3
    assert verdict.category == "scanning"
    assert verdict.confidence == 0.72
    assert verdict.escalate is False


def test_severity_out_of_range_rejected() -> None:
    for bad_severity in (0, 6):
        with pytest.raises(ValidationError):
            Verdict(**_verdict_kwargs(severity=bad_severity))


def test_unknown_category_rejected() -> None:
    with pytest.raises(ValidationError):
        Verdict(**_verdict_kwargs(category="lateral_movement"))


def test_reasoning_over_1200_rejected() -> None:
    with pytest.raises(ValidationError):
        Verdict(**_verdict_kwargs(reasoning="x" * 1201))


def test_confidence_out_of_range_rejected() -> None:
    for bad_confidence in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            Verdict(**_verdict_kwargs(confidence=bad_confidence))


def test_severity_ge_4_requires_escalate() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Verdict(**_verdict_kwargs(severity=4, escalate=False))

    assert "PRD §6.6" in str(exc_info.value)


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Verdict(**_verdict_kwargs(unexpected_field="nope"))
