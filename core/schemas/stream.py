"""`VerdictCreatedEvent`: the validated shape of one `verdict.created` pub/sub message
(PRD §8 `/stream` row, §10.6) — m8a task-01.

`api/routes/stream.py::render_verdict_event` validates every raw Redis message against this model
before it is ever turned into wire bytes. `model_dump_json()` is what escapes `\\n`/`\\r` inside
`summary` (attacker-influenced model text, PRD §10.6), so a forged newline in it can never start a
second physical SSE line.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from core.schemas.alerts_read import REASONING_EXCERPT_CHARS
from core.schemas.verdict import VerdictCategory


class VerdictCreatedEvent(BaseModel):
    """One `verdict.created` message, validated on the way OUT of Redis and back to JSON."""

    model_config = ConfigDict(extra="forbid")

    alert_id: uuid.UUID
    verdict_id: uuid.UUID
    severity: Annotated[int, Field(ge=1, le=5)]
    category: VerdictCategory
    escalate: bool
    summary: Annotated[str, Field(max_length=REASONING_EXCERPT_CHARS)]
