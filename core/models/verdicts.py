"""`verdicts` — one row per triage run; retriage creates a new row (PRD §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, uuid_pk


class VerdictRow(Base):
    """One row per triage run against an alert (PRD §5).

    Retriage creates a new row rather than updating an existing one; the dashboard shows the
    latest verdict per alert (`ix_verdicts_alert_id_created_at`).
    """

    __tablename__ = "verdicts"
    __table_args__ = (
        CheckConstraint("severity BETWEEN 1 AND 5", name="ck_verdicts_severity"),
        Index("ix_verdicts_alert_id_created_at", "alert_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"))
    severity: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(REAL)
    reasoning: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(Text)
    escalate: Mapped[bool] = mapped_column(Boolean)
    model_primary: Mapped[str] = mapped_column(Text)
    model_final: Mapped[str] = mapped_column(Text)
    escalated_model: Mapped[bool] = mapped_column(Boolean)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
