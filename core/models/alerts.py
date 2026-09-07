"""`alerts` — one row per deduplicated incoming session alert (PRD §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, uuid_pk

AlertStatus = Literal["pending", "triaged", "failed"]


class AlertRow(Base):
    """One row per deduplicated incoming alert (PRD §5).

    `fingerprint` is the ingest dedup key (`sha256(source || session_id || connect_time)`,
    PRD §6.1) and is UNIQUE. `raw` is the full session payload as received; `ix_alerts_src_ip`
    is an expression index on `raw ->> 'src_ip'` for `get_alert_history` (PRD §6.3).
    """

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_received_at", text("received_at DESC")),
        Index("ix_alerts_src_ip", text("(raw ->> 'src_ip')")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    fingerprint: Mapped[str] = mapped_column(Text, unique=True, index=True)
    source: Mapped[str] = mapped_column(Text)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
