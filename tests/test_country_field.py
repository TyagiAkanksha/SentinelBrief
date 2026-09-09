"""Pins `country` on the alert DTOs (PRD §9, deferred from M3; m4 task-07): `normalize_country`,
`AlertBase.country`'s optional default, the list/detail derivation from the latest verdict's
*first* `get_ip_geo_asn` result, and the wire serialization through both read routes.

M3 statement-count baselines, measured at HEAD before this task (`before_cursor_execute`
listener, the same technique `tests/test_alerts_read_service.py` uses): `list_alerts` compiles
**2** statements (one `count(*)` + one items `SELECT`); `get_alert_detail` on an alert with a
verdict and tool calls compiles **3** statements (alert, latest verdict, tool_calls). The geo
join task-07 adds to `list_alerts`'s `base` select and the Python-side derivation in
`get_alert_detail` must not move either number —
`test_list_alerts_with_geo_join_still_projects_raw_fields_only` and
`test_get_alert_detail_country_from_its_own_tool_calls` pin both counts directly.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.schemas.alerts_read import AlertSummary, ListFilters, normalize_country
from core.schemas.verdict import Verdict, VerdictCategory
from core.services.alerts_read import GEO_TOOL_NAME, get_alert_detail, list_alerts
from tests.helpers import add_verdict, seed_alert
from worker.store import ToolCallRecord

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# Same pin as tests/test_alerts_read_service.py::_BARE_RAW_COLUMN: `alerts.raw` may only ever
# appear followed by ` ->>` (the JSONB extraction operator) — never as a whole-column selection.
_BARE_RAW_COLUMN = re.compile(r"alerts\.raw(?!\s*->>)")


def _verdict(
    *,
    severity: int = 3,
    category: VerdictCategory = "scanning",
    escalate: bool = False,
    confidence: float = 0.7,
) -> Verdict:
    """A `Verdict` with sensible defaults, overridable per test."""
    return Verdict(
        severity=severity,
        category=category,
        confidence=confidence,
        reasoning="test reasoning",
        recommended_action="monitor",
        escalate=escalate,
    )


def test_normalize_country_accepts_only_two_uppercase_letters() -> None:
    assert normalize_country("DE") == "DE"

    for bad in ("de", "DEU", "", None, 7, "D<"):
        assert normalize_country(bad) is None


def test_country_is_optional_on_summary_and_detail_and_base_fields_still_shared() -> None:
    summary = AlertSummary(
        id=uuid.uuid4(),
        source="cowrie",
        src_ip="192.0.2.55",
        sensor="hp-eu-01",
        event_time=_NOW,
        received_at=_NOW,
        status="pending",
        verdict=None,
    )

    assert summary.country is None

    schema = create_app().openapi()["components"]["schemas"]["AlertSummary"]
    assert "country" not in schema.get("required", [])


async def test_list_alerts_country_comes_from_the_first_geo_call_of_the_latest_verdict(
    db_session: AsyncSession,
) -> None:
    alert_id = await seed_alert(
        db_session,
        session_id="geo-latest-wins",
        verdict=_verdict(),
        verdict_created_at=_NOW,
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "203.0.113.10"},
                result={"ip": "203.0.113.10", "country": "NL", "asn": 64496, "org": "Old ISP"},
                latency_ms=10,
            ),
        ),
    )
    await add_verdict(
        db_session,
        alert_id,
        _verdict(),
        created_at=_NOW + timedelta(hours=1),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name="get_session_commands",
                arguments={"session_id": "geo-latest-wins"},
                result={"commands": ["whoami"]},
                latency_ms=8,
            ),
            ToolCallRecord(
                seq=1,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "192.0.2.55"},
                result={"ip": "192.0.2.55", "country": "DE", "asn": 64499, "org": "New ISP"},
                latency_ms=12,
            ),
            ToolCallRecord(
                seq=2,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "192.0.2.55"},
                result={"ip": "192.0.2.55", "country": "FR", "asn": 64501, "org": "Later ISP"},
                latency_ms=9,
            ),
        ),
    )
    await db_session.commit()

    items, _total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    item = next(i for i in items if i.id == alert_id)
    assert item.country == "DE"


async def test_list_alerts_country_is_null_for_unavailable_missing_or_malformed_geo(
    db_session: AsyncSession,
) -> None:
    unavailable_id = await seed_alert(
        db_session,
        session_id="geo-unavailable",
        verdict=_verdict(),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "203.0.113.10"},
                result={"unavailable": True, "reason": "geoip_db_not_configured"},
                latency_ms=5,
            ),
        ),
    )
    no_geo_id = await seed_alert(
        db_session,
        session_id="geo-missing",
        verdict=_verdict(),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name="get_session_commands",
                arguments={"session_id": "geo-missing"},
                result={"commands": []},
                latency_ms=5,
            ),
        ),
    )
    malformed_id = await seed_alert(
        db_session,
        session_id="geo-malformed",
        verdict=_verdict(),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "203.0.113.10"},
                result={"ip": "203.0.113.10", "country": "xx", "asn": None, "org": None},
                latency_ms=5,
            ),
        ),
    )
    pending_id = await seed_alert(db_session, session_id="geo-pending")
    await db_session.commit()

    items, _total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    by_id = {item.id: item for item in items}
    assert by_id[unavailable_id].country is None
    assert by_id[no_geo_id].country is None
    assert by_id[malformed_id].country is None
    assert by_id[pending_id].country is None


async def test_list_alerts_with_geo_join_still_projects_raw_fields_only(
    db_session: AsyncSession,
) -> None:
    """I1 (carried from M3): the geo join must not turn the list into a whole-`raw`-loading
    query, and must not add a third statement — `list_alerts` still compiles exactly the M3
    baseline of 2 (one `count(*)` + one items `SELECT`)."""
    await seed_alert(
        db_session,
        "alert4",
        session_id="geo-sql-raw-001",
        verdict=_verdict(),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "192.0.2.55"},
                result={"ip": "192.0.2.55", "country": "DE", "asn": 64499, "org": "New ISP"},
                latency_ms=5,
            ),
        ),
    )
    await db_session.commit()

    statements: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements, "no SQL captured — the listener/bind wiring is broken"
    assert any("alerts.raw ->>" in stmt for stmt in statements)
    assert not any(_BARE_RAW_COLUMN.search(stmt) for stmt in statements)
    assert len(statements) == 2  # M3 baseline, measured at HEAD before this task


async def test_get_alert_detail_country_from_its_own_tool_calls(
    db_session: AsyncSession,
) -> None:
    de_alert_id = await seed_alert(
        db_session,
        session_id="detail-geo-de",
        verdict=_verdict(),
        verdict_created_at=_NOW,
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name="get_session_commands",
                arguments={"session_id": "detail-geo-de"},
                result={"commands": ["whoami"]},
                latency_ms=8,
            ),
            ToolCallRecord(
                seq=1,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "192.0.2.55"},
                result={"ip": "192.0.2.55", "country": "DE", "asn": 64499, "org": "New ISP"},
                latency_ms=12,
            ),
        ),
    )
    no_geo_id = await seed_alert(db_session, session_id="detail-geo-none", verdict=_verdict())
    await db_session.commit()

    statements: list[str] = []

    def _capture(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        detail = await get_alert_detail(db_session, de_alert_id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert detail.country == "DE"
    # M3 baseline, measured at HEAD before this task: alert + latest verdict + tool_calls.
    # Country is derived in Python from the already-loaded `tool_calls`, so it must not grow.
    assert len(statements) == 3

    no_geo_detail = await get_alert_detail(db_session, no_geo_id)
    assert no_geo_detail.country is None


async def test_list_and_detail_routes_serialize_country_and_baseline_matches(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=db_session_factory)

    async with db_session_factory() as session:
        alert_id = await seed_alert(
            session,
            "alert4",
            session_id="route-country-de",
            verdict=_verdict(),
            tool_calls=(
                ToolCallRecord(
                    seq=0,
                    tool_name=GEO_TOOL_NAME,
                    arguments={"ip": "192.0.2.55"},
                    result={"ip": "192.0.2.55", "country": "DE", "asn": 64499, "org": "New ISP"},
                    latency_ms=5,
                ),
            ),
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_response = await client.get("/api/v1/alerts")
        detail_response = await client.get(f"/api/v1/alerts/{alert_id}")

    list_item = next(item for item in list_response.json()["items"] if item["id"] == str(alert_id))
    assert list_item["country"] == "DE"
    assert detail_response.json()["country"] == "DE"
