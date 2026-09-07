"""Golden-set case loader: `GoldenLabel`, `GoldenCase`, `load_golden` (PRD §7.1, §13).

PRD §7.1: the golden set is a JSONL file, one labeled session per line, used to develop and score
the triage pipeline (v1: ~20 synthetic sessions, build-time only, never published; v2: >=200 real
sessions, hand-labeled by the author, publish-quality). §13: v1 rows and labels may be
machine-authored synthetic data; v2 labels are human work and are never generated here.

This module is a package (mirroring `worker/prompts/`) so the loader and the dataset files it
validates (`v1.jsonl`, `README.md`) live side by side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, ValidationError, model_validator

from core.schemas.alert import SessionAlert
from core.schemas.verdict import VerdictCategory


class GoldenLabel(BaseModel):
    """The ground-truth verdict fields for one golden-set case (PRD §6.6 rubric)."""

    severity: Annotated[int, Field(ge=1, le=5)]
    category: VerdictCategory
    escalate: bool

    @model_validator(mode="after")
    def _escalate_required_for_high_severity(self) -> GoldenLabel:
        """Enforce PRD §6.6: severity >= 4 requires escalate == True.

        Mirrors `core.schemas.verdict.Verdict`'s rule so a golden label can never itself violate
        the rubric it is meant to score the pipeline against.
        """
        if self.severity >= 4 and not self.escalate:
            raise ValueError("escalate must be true for severity >= 4 (PRD §6.6)")
        return self


class GoldenCase(BaseModel):
    """One labeled golden-set row: a session alert plus its ground-truth label (PRD §7.1)."""

    alert: SessionAlert
    label: GoldenLabel
    labeler_note: Annotated[str, Field(min_length=10)]
    tags: list[str] = []

    @property
    def case_id(self) -> str:
        """The case's identity: `alert.fingerprint()` (PRD §6.1 dedup key, reused as case id)."""
        return self.alert.fingerprint()


def load_golden(path: Path) -> list[GoldenCase]:
    """Load and validate every golden-set case from a JSONL file (PRD §7.1).

    Args:
        path: Path to a golden-set file (e.g. `evals/golden/v1.jsonl`), one JSON object per
            non-empty line.

    Returns:
        One `GoldenCase` per non-empty line, in file order.

    Raises:
        ValueError: A row is not valid JSON or fails `GoldenCase` validation
            (`"row {n}: {error}"`, 1-indexed over non-empty lines), or a row's `case_id`
            duplicates an earlier row's (PRD §13: v1 rows must have unique ids for scoring).
    """
    cases: list[GoldenCase] = []
    row_of_case_id: dict[str, int] = {}
    row_number = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row_number += 1
        try:
            payload = json.loads(line)
            case = GoldenCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as err:
            raise ValueError(f"row {row_number}: {err}") from err
        if case.case_id in row_of_case_id:
            raise ValueError(
                f"row {row_number}: duplicate case_id {case.case_id!r} "
                f"(first seen at row {row_of_case_id[case.case_id]})"
            )
        row_of_case_id[case.case_id] = row_number
        cases.append(case)
    return cases
