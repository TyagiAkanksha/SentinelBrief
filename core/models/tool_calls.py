"""`tool_calls` — full trace of the enrichment loop, per verdict (PRD §5)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, uuid_pk


class ToolCallRow(Base):
    """One row per tool invocation within a verdict's enrichment loop (PRD §5)."""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_verdict_id_seq", "verdict_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    verdict_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("verdicts.id"))
    seq: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
