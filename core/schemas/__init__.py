"""Pydantic DTOs: Verdict (PRD §6.5), SessionAlert, ingest/response shapes (CONVENTIONS.md §2)."""

from __future__ import annotations

from core.schemas.alert import CowrieEvent, SessionAlert
from core.schemas.alerts_read import (
    AlertDetail,
    AlertSummary,
    DayVolume,
    ListFilters,
    StatsOut,
    ToolCallOut,
    VerdictOut,
    VerdictSummary,
)
from core.schemas.errors import ErrorBody, ErrorEnvelope
from core.schemas.pagination import PaginatedResponse
from core.schemas.verdict import VERDICT_JSON_SCHEMA, Verdict, VerdictCategory

__all__ = [
    "VERDICT_JSON_SCHEMA",
    "AlertDetail",
    "AlertSummary",
    "CowrieEvent",
    "DayVolume",
    "ErrorBody",
    "ErrorEnvelope",
    "ListFilters",
    "PaginatedResponse",
    "SessionAlert",
    "StatsOut",
    "ToolCallOut",
    "Verdict",
    "VerdictCategory",
    "VerdictOut",
    "VerdictSummary",
]
