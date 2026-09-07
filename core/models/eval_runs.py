"""`eval_runs` — one row per evaluation harness execution (PRD §5, §7.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, uuid_pk


class EvalRunRow(Base):
    """One row per `evals.run` execution; `metrics` carries the full PRD §7.3 metrics blob."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    git_sha: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    model_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
