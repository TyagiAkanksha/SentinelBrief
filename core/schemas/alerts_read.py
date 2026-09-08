"""Read-side DTOs for the alert list/detail/stats views (PRD §5, §8) — m3 task-01.

Frozen before any read route exists: `core.services.alerts_read` builds these directly from the
latest-verdict query, and `api`'s M3 routes (task-02) declare them as `response_model`s. The
`AlertBase` split keeps `AlertSummary` (list rows) and `AlertDetail` (single-alert view) sharing
one set of envelope fields while differing by exactly the fields the detail view adds.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.models import AlertStatus
from core.schemas.verdict import VerdictCategory

REASONING_EXCERPT_CHARS = 160


def reasoning_excerpt(reasoning: str) -> str:
    """The first `REASONING_EXCERPT_CHARS` characters of `reasoning`, a plain slice, no ellipsis."""
    return reasoning[:REASONING_EXCERPT_CHARS]


class VerdictOut(BaseModel):
    """Every `verdicts` column except `alert_id` (the row's parent is implicit in its context)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    severity: int
    category: VerdictCategory
    confidence: float
    reasoning: str
    recommended_action: str
    escalate: bool
    model_primary: str
    model_final: str
    escalated_model: bool
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
    # `Decimal`'s default pydantic v2 JSON serialization is a string (numeric(10,6) stays
    # lossless on the wire); the web formats it (task-04 `formatUsd`). No custom serializer.
    cost_usd: Decimal | None
    latency_ms: int | None
    created_at: datetime


class VerdictSummary(BaseModel):
    """The list-row projection of a verdict: enough to sort/filter/render a summary line."""

    severity: int
    category: VerdictCategory
    confidence: float
    escalate: bool
    reasoning_excerpt: str
    created_at: datetime


class ToolCallOut(BaseModel):
    """One `tool_calls` row, as returned in an alert's detail view."""

    model_config = ConfigDict(from_attributes=True)

    seq: int
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    latency_ms: int | None


class AlertBase(BaseModel):
    """Fields shared by every alert view; never used directly as a `response_model`."""

    id: uuid.UUID
    source: str
    src_ip: str
    sensor: str
    event_time: datetime
    received_at: datetime
    status: AlertStatus


class AlertSummary(AlertBase):
    """One list row (PRD §8): the latest verdict's summary, or `None` for pending/failed alerts."""

    verdict: VerdictSummary | None


class AlertDetail(AlertBase):
    """The single-alert view (PRD §8): full raw payload, latest verdict, and its tool calls."""

    raw: dict[str, Any]
    verdict: VerdictOut | None
    tool_calls: list[ToolCallOut]


class DayVolume(BaseModel):
    """One day's alert count, for the stats view's `volume_by_day` series."""

    day: date
    count: int


class StatsOut(BaseModel):
    """The dashboard stats view (PRD §8): volume by day, distributions, cost, and latency."""

    total_alerts: int
    by_status: dict[str, int]
    by_severity: dict[str, int]
    by_category: dict[str, int]
    escalated_count: int
    volume_by_day: list[DayVolume]
    cost_total_usd: Decimal
    cost_mean_usd: Decimal
    latency_p50_ms: int
    latency_p95_ms: int
    last_alert_at: datetime | None


class ListFilters(BaseModel):
    """The PRD §8 alert-list filters. Each verdict-field filter naturally excludes alerts
    without a verdict yet (a `NULL` never satisfies a comparison)."""

    severity_gte: Annotated[int, Field(ge=1, le=5)] | None = None
    category: VerdictCategory | None = None
    since: datetime | None = None
    escalate: bool | None = None

    @field_validator("since")
    @classmethod
    def _since_naive_is_utc(cls, value: datetime | None) -> datetime | None:
        """A naive `since` is treated as UTC rather than the server's local time."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
