"""`Verdict`: the structured-output contract for triage (PRD §6.5, §6.6).

The verdict schema **is** this Pydantic model. Its JSON schema (`VERDICT_JSON_SCHEMA`) is embedded
in the triage prompt (task-04) and the LLM's reply is validated against this model on return; one
retry with the validation error appended, second failure -> `VerdictValidationError` (§6.2).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VerdictCategory = Literal[
    "scanning",
    "brute_force",
    "successful_intrusion",
    "malware_delivery",
    "persistence_attempt",
    "reconnaissance",
    "other",
]


class Verdict(BaseModel):
    """One triage verdict for a session alert (PRD §6.5); extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    severity: Annotated[int, Field(ge=1, le=5)]
    category: VerdictCategory
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: Annotated[str, Field(max_length=1200)]
    recommended_action: Annotated[str, Field(max_length=300)]
    escalate: bool

    @model_validator(mode="after")
    def _escalate_required_for_high_severity(self) -> Verdict:
        """Enforce PRD §6.6: severity >= 4 requires escalate == True.

        A violation is a structured-output validation failure, so it gets the one PRD §6.5 retry
        rather than silently shipping an under-escalated high-severity verdict.
        """
        if self.severity >= 4 and not self.escalate:
            raise ValueError("escalate must be true for severity >= 4 (PRD §6.6)")
        return self


VERDICT_JSON_SCHEMA = Verdict.model_json_schema()
