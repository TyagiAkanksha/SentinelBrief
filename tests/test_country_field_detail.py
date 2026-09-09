"""m4 task-07 fix round 1, Part A: pins the review's Findings I1, M1, M2, M3, M4
(`.superpowers/sdd/m4-tool-calling/task-07-review.md`) against `core/services/alerts_read.py`'s
`get_alert_detail`/`list_alerts` country derivation, which `tests/test_country_field.py` left
under-pinned.

I1 — the detail-side derivation (`core/services/alerts_read.py:204-206`) survived three
independent mutations because the original fixture (`tests/test_country_field.py`'s
`test_get_alert_detail_country_from_its_own_tool_calls`) seeded a single, already-valid geo call.
This file's `test_get_alert_detail_country_picks_the_first_geo_call_not_the_last` gives the
non-geo tool call a *spurious* `country` key (`get_session_commands` returning `{"country":
"ZZ"}` alongside its real payload) — a case the review's Finding I1 "Fix shape" names explicitly
— so that a result-key filter (rather than a tool-name filter) picks the wrong row.

M1 — `normalize_country`'s `$` anchor accepts a trailing `\\n` (Python `re` semantics); the TS
`countryFlag` counterpart does not (a spec defect in the brief's own Interfaces line, ruled to be
fixed with `re.fullmatch` in Part B).

M2 — `list_alerts`'s `count(*)` statement inlines the geo `LEFT JOIN` (and the `tool_calls` table
it scans) even though the join cannot change the row count (1:0..1 on `latest.id`); ruled to be
removed from the COUNT statement only, in Part B. Note `latest_verdicts_subquery()` also compiles
its own `DISTINCT ON (verdicts.alert_id)`, needed by the COUNT statement for its filters and
therefore unrelated to this finding — the scoped check here is "no `tool_calls` reference",
which is what actually distinguishes the geo join's presence (see Judgment calls in the
test-author report for why a bare "no DISTINCT ON anywhere" check would be unsatisfiable).

M3 — two verified-but-unpinned properties: the geo join cannot fan out a list row, and the API
always sends the `country` key even when its value is `null`.

M4 — `GEO_TOOL_NAME` (`core/services/alerts_read.py`) duplicates `GeoAsnTool.name`
(`worker/tools/geo_asn.py`) with nothing tying them together (forced by the import-linter
contract that forbids `core` importing `worker`); `tests/` sits outside that contract and can pin
the equality directly.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.schemas.alerts_read import ListFilters, normalize_country
from core.schemas.verdict import Verdict, VerdictCategory
from core.services.alerts_read import GEO_TOOL_NAME, get_alert_detail, list_alerts
from tests.helpers import seed_alert
from worker.store import ToolCallRecord


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


# --- M1: normalize_country's `$` anchor accepts a trailing newline (RED at HEAD) --------------


def test_normalize_country_rejects_trailing_newline_or_carriage_return() -> None:
    # Python's `$` (without `re.MULTILINE`) matches at the end of the string OR immediately
    # before a trailing "\n" — so `"DE\n"` wrongly survives today. `"\r"` has no such special
    # case and already returns `None`; both are asserted so a future `fullmatch`/`\Z` fix is
    # pinned against both line-ending styles at once.
    assert normalize_country("DE\n") is None
    assert normalize_country("DE\r") is None


# --- I1: the detail-side derivation, discriminating fixtures ------------------------------------


async def test_get_alert_detail_country_picks_the_first_geo_call_not_the_last(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Kills P3 (reversed iteration picks the *last* geo call) and P9 (filtering by "has a
    `country` key" instead of by tool name): `get_session_commands` here returns a result that
    happens to carry a spurious `country` key of its own, ahead of the real geo calls in `seq`
    order — a result-key filter would pick `"ZZ"` first; a reversed scan would pick the *last*
    geo call's `"FR"`. Only "first call whose `tool_name == GEO_TOOL_NAME`" yields `"DE"`.
    Checked through both the service function and the HTTP detail route.
    """
    app = create_app(session_factory=db_session_factory)

    async with db_session_factory() as session:
        alert_id = await seed_alert(
            session,
            session_id="detail-first-wins",
            verdict=_verdict(),
            tool_calls=(
                ToolCallRecord(
                    seq=0,
                    tool_name="get_session_commands",
                    arguments={"session_id": "detail-first-wins"},
                    result={"commands": ["whoami"], "country": "ZZ"},
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
        await session.commit()

    async with db_session_factory() as session:
        detail = await get_alert_detail(session, alert_id)
    assert detail.country == "DE"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/alerts/{alert_id}")
    assert response.json()["country"] == "DE"


async def test_get_alert_detail_country_normalizes_a_malformed_geo_result(
    db_session: AsyncSession,
) -> None:
    """Kills P8 (dropping the `normalize_country(...)` wrapper): with no non-geo call to
    confound a result-key filter, this alert isolates *only* the normalization behavior — an
    unwrapped `"xx"` would leak onto `AlertDetail.country` unchanged.
    """
    alert_id = await seed_alert(
        db_session,
        session_id="detail-malformed-geo",
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
    await db_session.commit()

    detail = await get_alert_detail(db_session, alert_id)
    assert detail.country is None


async def test_get_alert_detail_country_first_call_wins_even_when_invalid(
    db_session: AsyncSession,
) -> None:
    """The brief's "first `get_ip_geo_asn` result" rule is a positional rule, not a
    first-*valid*-result rule: an invalid first geo call must not be skipped in favor of a valid
    later one."""
    alert_id = await seed_alert(
        db_session,
        session_id="detail-first-invalid-wins",
        verdict=_verdict(),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "203.0.113.10"},
                result={"ip": "203.0.113.10", "country": "de", "asn": 1, "org": "x"},
                latency_ms=5,
            ),
            ToolCallRecord(
                seq=1,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "203.0.113.10"},
                result={"ip": "203.0.113.10", "country": "DE", "asn": 2, "org": "y"},
                latency_ms=5,
            ),
        ),
    )
    await db_session.commit()

    detail = await get_alert_detail(db_session, alert_id)
    assert detail.country is None


# --- M2: the COUNT statement must not carry the geo join (RED at HEAD) -------------------------


async def test_list_alerts_count_statement_omits_the_geo_join(db_session: AsyncSession) -> None:
    await seed_alert(
        db_session,
        "alert4",
        session_id="count-no-geo-join",
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

    assert len(statements) == 2
    count_sql, items_sql = statements
    # "tool_calls" appearing at all in the COUNT statement means the geo join (and its
    # `DISTINCT ON (tool_calls.verdict_id) ... FROM tool_calls` subquery) rode along —
    # `latest_verdicts_subquery()`'s own `DISTINCT ON (verdicts.alert_id)` is unrelated and stays
    # in both statements (needed by the COUNT's own filters), so this check is scoped to the
    # `tool_calls` reference specifically, not a bare "no DISTINCT ON anywhere" claim.
    assert "tool_calls" not in count_sql
    assert "tool_calls" in items_sql


# --- M3: no fan-out; the country key is always on the wire (already true — pins, not RED) ------


async def test_list_alerts_two_geo_calls_do_not_fan_out(db_session: AsyncSession) -> None:
    alert_id = await seed_alert(
        db_session,
        session_id="fanout-two-geo",
        verdict=_verdict(),
        tool_calls=(
            ToolCallRecord(
                seq=0,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "192.0.2.55"},
                result={"ip": "192.0.2.55", "country": "DE", "asn": 1, "org": "x"},
                latency_ms=5,
            ),
            ToolCallRecord(
                seq=1,
                tool_name=GEO_TOOL_NAME,
                arguments={"ip": "192.0.2.55"},
                result={"ip": "192.0.2.55", "country": "FR", "asn": 2, "org": "y"},
                latency_ms=5,
            ),
        ),
    )
    await db_session.commit()

    items, total = await list_alerts(db_session, filters=ListFilters(), page=1, page_size=10)

    matching = [item for item in items if item.id == alert_id]
    assert len(matching) == 1
    assert total == 1


async def test_list_alerts_route_sends_country_key_even_when_null(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=db_session_factory)

    async with db_session_factory() as session:
        await seed_alert(session, session_id="wire-country-null")
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/alerts")

    items = response.json()["items"]
    assert len(items) == 1
    assert "country" in items[0]
    assert items[0]["country"] is None


# --- M4: GEO_TOOL_NAME is tied to the tool's own name -------------------------------------------


def test_geo_tool_name_matches_the_geo_asn_tool_class_name() -> None:
    from worker.tools import GeoAsnTool

    assert GeoAsnTool.name == GEO_TOOL_NAME
