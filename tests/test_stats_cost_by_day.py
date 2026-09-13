"""Pins `StatsOut.cost_by_day` — the per-day cost series `core/services/alerts_read.py::get_stats`
computes alongside its existing distributions (PRD §8, §9) — m8a task-02.

`DayCost` is keyed on the *alert's* received day (briefing ruling R5): a retriage weeks later
still counts against the day the traffic arrived, so this series shares an x-axis with
`volume_by_day`. Every seeded row goes through `tests.helpers.seed_alert` / `add_verdict`, the
production writers, and every `Decimal` assertion below compares against a `Decimal(...)` literal,
never a float — a float comparison would hide the exact `_SIX_DP` + `ROUND_HALF_UP` quantization
this module pins.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from core.schemas.alerts_read import DayCost
from core.schemas.verdict import Verdict, VerdictCategory
from core.services.alerts_read import get_stats
from tests.helpers import add_verdict, seed_alert

_DAY_A = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
_DAY_B = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


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


async def test_cost_by_day_sums_verdict_cost_per_received_day(db_session: AsyncSession) -> None:
    await seed_alert(
        db_session,
        session_id="cbd-a1",
        verdict=_verdict(),
        received_at=_DAY_A,
        cost_usd=Decimal("0.000100"),
    )
    await seed_alert(
        db_session,
        session_id="cbd-a2",
        verdict=_verdict(),
        received_at=_DAY_A,
        cost_usd=Decimal("0.000200"),
    )
    await seed_alert(
        db_session,
        session_id="cbd-b1",
        verdict=_verdict(),
        received_at=_DAY_B,
        cost_usd=Decimal("0.000050"),
    )
    await db_session.commit()

    stats = await get_stats(db_session)

    assert stats.cost_by_day == [
        DayCost(
            day=date(2026, 9, 1),
            alerts=2,
            cost_usd=Decimal("0.000300"),
            mean_cost_usd=Decimal("0.000150"),
        ),
        DayCost(
            day=date(2026, 9, 2),
            alerts=1,
            cost_usd=Decimal("0.000050"),
            mean_cost_usd=Decimal("0.000050"),
        ),
    ]


async def test_cost_by_day_counts_an_alert_once_when_it_has_two_verdicts(
    db_session: AsyncSession,
) -> None:
    """Load-bearing (Interfaces → test table): `count(DISTINCT alerts.id)` is what keeps a
    retriaged alert from being counted twice. Swapping it for a bare `count(alerts.id)` must fail
    this test — the LEFT JOIN would otherwise double `alerts` for this one alert's two verdicts
    and drag `mean_cost_usd` down to half its true value."""
    alert_id = await seed_alert(
        db_session,
        session_id="cbd-retriage",
        verdict=_verdict(),
        received_at=_DAY_A,
        cost_usd=Decimal("0.000100"),
    )
    await add_verdict(
        db_session,
        alert_id,
        _verdict(severity=5, escalate=True),
        cost_usd=Decimal("0.000050"),
    )
    await db_session.commit()

    stats = await get_stats(db_session)

    assert len(stats.cost_by_day) == 1
    row = stats.cost_by_day[0]
    assert row.day == date(2026, 9, 1)
    assert row.alerts == 1
    assert row.cost_usd == Decimal("0.000150")
    assert row.mean_cost_usd == row.cost_usd


async def test_cost_by_day_mean_is_quantized_to_six_places_half_up(
    db_session: AsyncSession,
) -> None:
    for session_id, cost in (
        ("cbd-mean-1", Decimal("0.000034")),
        ("cbd-mean-2", Decimal("0.000033")),
        ("cbd-mean-3", Decimal("0.000033")),
    ):
        await seed_alert(
            db_session,
            session_id=session_id,
            verdict=_verdict(),
            received_at=_DAY_A,
            cost_usd=cost,
        )
    await db_session.commit()

    stats = await get_stats(db_session)

    assert len(stats.cost_by_day) == 1
    row = stats.cost_by_day[0]
    assert row.alerts == 3
    assert row.cost_usd == Decimal("0.000100")
    assert row.mean_cost_usd == Decimal("0.000033")
    assert row.mean_cost_usd.as_tuple().exponent == -6


async def test_cost_by_day_counts_alerts_with_no_verdict_at_zero_cost(
    db_session: AsyncSession,
) -> None:
    await seed_alert(db_session, session_id="cbd-pending", received_at=_DAY_A)
    await db_session.commit()

    stats = await get_stats(db_session)

    assert len(stats.cost_by_day) == 1
    row = stats.cost_by_day[0]
    assert row.day == date(2026, 9, 1)
    assert row.alerts == 1
    assert row.cost_usd == Decimal("0")
    assert row.mean_cost_usd == Decimal("0")


async def test_cost_by_day_is_empty_on_an_empty_database(db_session: AsyncSession) -> None:
    stats = await get_stats(db_session)

    assert stats.cost_by_day == []
    # Every existing field stays zero-filled (unchanged by this task) — a regression here would
    # mean the new query broke the pre-existing zero-fill contract, not just added a new field.
    assert stats.total_alerts == 0
    assert stats.volume_by_day == []
    assert stats.cost_total_usd == Decimal("0")


async def test_stats_response_carries_cost_by_day(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(
        session_factory=db_session_factory,
        settings=Settings(ingest_hmac_secret=SecretStr("test-secret")),
    )
    async with db_session_factory() as session:
        await seed_alert(
            session,
            session_id="cbd-route",
            verdict=_verdict(),
            cost_usd=Decimal("0.000228"),
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200
    body = response.json()
    assert "cost_by_day" in body
    rows = body["cost_by_day"]
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == {"day", "alerts", "cost_usd", "mean_cost_usd"}
    # PRD §8's Decimal-as-JSON-string convention (the same one `VerdictOut.cost_usd` already
    # follows) must hold for the new series too — a bare float would silently lose precision.
    assert isinstance(row["cost_usd"], str)
    assert isinstance(row["mean_cost_usd"], str)
