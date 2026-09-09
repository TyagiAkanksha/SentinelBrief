"""Pins the M3 read-side wire DTOs (PRD §8): the generic pagination envelope, the error
envelope, and the alert/verdict/stats view models — frozen before any read route exists
(m3 task-01).

`test_verdict_out_covers_every_verdict_column_except_alert_id` is the load-bearing one: it pins
`VerdictOut` against `VerdictRow.__table__.columns` directly, so a future `verdicts` column that
forgets to widen the DTO fails here rather than silently dropping data on the wire.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.models import VerdictRow
from core.schemas.alerts_read import (
    REASONING_EXCERPT_CHARS,
    AlertBase,
    AlertDetail,
    AlertSummary,
    ListFilters,
    VerdictOut,
    reasoning_excerpt,
)
from core.schemas.errors import ErrorBody, ErrorEnvelope
from core.schemas.pagination import PaginatedResponse

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _make_verdict_out(**overrides: object) -> VerdictOut:
    """A `VerdictOut` covering every field, overridable per test."""
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "severity": 4,
        "category": "successful_intrusion",
        "confidence": 0.9,
        "reasoning": "attacker logged in as root and ran reconnaissance commands",
        "recommended_action": "isolate host and rotate credentials",
        "escalate": True,
        "model_primary": "fake-model",
        "model_final": "fake-model",
        "escalated_model": False,
        "prompt_version": "triage-v1",
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": Decimal("0.000100"),
        "latency_ms": 5,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return VerdictOut.model_validate(defaults)


def test_paginated_response_is_generic_over_item_type() -> None:
    int_page = PaginatedResponse[int](items=[1, 2, 3], total=3, page=1, page_size=20)
    assert int_page.items == [1, 2, 3]

    alert_summary = AlertSummary(
        id=uuid.uuid4(),
        source="cowrie",
        src_ip="192.0.2.55",
        sensor="hp-eu-01",
        event_time=_NOW,
        received_at=_NOW,
        status="pending",
        verdict=None,
    )
    alert_page = PaginatedResponse[AlertSummary](
        items=[alert_summary], total=1, page=1, page_size=20
    )
    assert alert_page.items == [alert_summary]

    with pytest.raises(ValidationError):
        PaginatedResponse[int](items=[1], page=1, page_size=20)  # missing `total`


def test_error_envelope_matches_prd_shape() -> None:
    envelope = ErrorEnvelope(error=ErrorBody(code="not_found", message="alert not found"))

    assert envelope.model_dump() == {"error": {"code": "not_found", "message": "alert not found"}}


def test_verdict_out_covers_every_verdict_column_except_alert_id() -> None:
    expected = {c.name for c in VerdictRow.__table__.columns} - {"alert_id"}

    assert set(VerdictOut.model_fields) == expected


def test_verdict_out_serializes_cost_usd_as_decimal_string() -> None:
    priced = _make_verdict_out(cost_usd=Decimal("0.000228"))
    unpriced = _make_verdict_out(cost_usd=None)

    assert priced.model_dump(mode="json")["cost_usd"] == "0.000228"
    assert unpriced.model_dump(mode="json")["cost_usd"] is None


def test_reasoning_excerpt_is_first_160_chars() -> None:
    long_reasoning = "x" * 200
    short_reasoning = "y" * 10

    excerpt = reasoning_excerpt(long_reasoning)
    assert excerpt == "x" * REASONING_EXCERPT_CHARS
    assert len(excerpt) == 160
    assert reasoning_excerpt(short_reasoning) == short_reasoning


def test_list_filters_reject_out_of_range_severity_and_unknown_category() -> None:
    with pytest.raises(ValidationError):
        ListFilters(severity_gte=0)
    with pytest.raises(ValidationError):
        ListFilters(severity_gte=6)
    with pytest.raises(ValidationError):
        ListFilters(category="bogus")  # type: ignore[arg-type]


def test_list_filters_since_naive_is_treated_as_utc() -> None:
    naive = ListFilters(since=datetime(2026, 9, 1, 12, 0))
    aware = ListFilters(since=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))

    assert naive.since is not None
    assert naive.since.tzinfo == UTC
    assert aware.since == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_alert_detail_and_summary_share_base_fields() -> None:
    base_fields = set(AlertBase.model_fields)
    summary_fields = set(AlertSummary.model_fields)
    detail_fields = set(AlertDetail.model_fields)

    assert summary_fields - base_fields == {"verdict"}
    assert detail_fields - summary_fields == {"raw", "tool_calls"}
    assert (
        AlertSummary.model_fields["verdict"].annotation
        != AlertDetail.model_fields["verdict"].annotation
    )
