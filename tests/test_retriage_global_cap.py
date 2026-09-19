"""Pins the retriage daily cap as one GLOBAL counter shared across every admin source IP, never
per-IP (m8b whole-repo review, finding I4; PRD §8, §10.2). MUT-D (a per-IP counter key,
`retriage:{client_ip}:{date}` instead of `retriage:{date}`) survived every test in
`tests/test_admin_retriage.py` (pinned) because each of those tests uses one `ASGITransport` peer
throughout — this file drives `RETRIAGE_PER_DAY` retriages from TWO DIFFERENT client IPs (the
`ASGITransport(client=...)` idiom `tests/test_rate_limit.py` already uses) with a valid admin
token, then proves the (cap+1)th call from a THIRD, previously-unseen IP is still `429`: a per-IP
cap would let it through as a fresh bucket.

The retriage route is admin-token gated and never on the public-GET `rate_limit` limiter (m8b
review I2's private-IP exemption only touches `rate_limit`), so the client IPs chosen here carry
no special meaning beyond "three distinct peers."
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from arq.connections import ArqRedis
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from core.models import AlertStatus
from tests.helpers import seed_alert

ADMIN_TOKEN = "test-admin-token-global-cap"  # noqa: S105 -- fixture literal, not a real secret


@dataclass
class FakeEnqueue:
    """The fake of the external queue seam (`api.deps.EnqueueFn`), never our own code (mirrors
    `tests/test_admin_retriage.py::FakeEnqueue`)."""

    calls: list[uuid.UUID] = field(default_factory=list)

    async def __call__(self, alert_id: uuid.UUID) -> None:
        self.calls.append(alert_id)


def _settings(**overrides: object) -> Settings:
    return Settings(admin_token=SecretStr(ADMIN_TOKEN), **overrides)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


async def _seed(
    session_factory: async_sessionmaker[AsyncSession], *, session_id: str, status: AlertStatus
) -> uuid.UUID:
    async with session_factory() as session:
        alert_id = await seed_alert(session, session_id=session_id, status=status)
        await session.commit()
        return alert_id


async def test_daily_cap_is_global_across_client_ips_not_per_ip(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """`RETRIAGE_PER_DAY=2` here (never the production default of 20) purely to keep the test
    cheap -- the mechanism pinned (one global Redis counter) is identical at any cap value."""
    fake_enqueue = FakeEnqueue()
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(retriage_per_day=2),
        enqueue=fake_enqueue,
        redis=arq_redis,
    )
    alert_a = await _seed(db_session_factory, session_id="retriage-global-a", status="triaged")
    alert_b = await _seed(db_session_factory, session_id="retriage-global-b", status="triaged")

    ip_a = ASGITransport(app=app, client=("10.1.0.1", 1))
    ip_b = ASGITransport(app=app, client=("10.1.0.2", 1))
    ip_c = ASGITransport(app=app, client=("10.1.0.3", 1))

    async with AsyncClient(transport=ip_a, base_url="http://test") as client:
        first = await client.post(f"/api/v1/admin/retriage/{alert_a}", headers=_auth())
    async with AsyncClient(transport=ip_b, base_url="http://test") as client:
        second = await client.post(f"/api/v1/admin/retriage/{alert_b}", headers=_auth())
    async with AsyncClient(transport=ip_c, base_url="http://test") as client:
        third = await client.post(f"/api/v1/admin/retriage/{uuid.uuid4()}", headers=_auth())

    assert first.status_code == 202
    assert second.status_code == 202
    # A third, never-before-seen client IP is still blocked: the cap is one GLOBAL counter, not
    # per-IP (MUT-D would let this succeed -- as a 404, since the DB would never even be reached
    # with a made-up id, proving whether the cap check ran before or after the lookup).
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert fake_enqueue.calls == [alert_a, alert_b]
