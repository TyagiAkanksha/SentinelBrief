"""Pins `POST /api/v1/admin/retriage/{alert_id}` end to end (PRD §10, §6.1 idempotency, the m5
task-04 deferral "the retriage flip needs `SET LOCAL lock_timeout` + 409"; m8b task-04).

The route does not exist yet — `api/routes/admin.py` — so every request below 404s today; that
is this file's RED signal. `require_admin_token`'s own 401 branches are pinned directly, in
isolation, by `tests/test_admin_auth.py`; this file drives the full route through a real DB +
Redis, mirroring `tests/test_ingest.py`'s pattern. `FakeEnqueue` is the fake of the external queue
seam (`api.deps.EnqueueFn`), never a `worker` double — retriage is the ONLY non-nightly path
allowed to cause an LLM call, and this file only ever asserts "the wired enqueue callable was
called with the alert's id", never a real triage run (PRD §10.1).

The lock-contention 409 path (`SET LOCAL lock_timeout` timing out against a row another
transaction holds under `FOR UPDATE`) is the implementer's to build and is not pinned here — it
is hard to drive deterministically without racing two real transactions, and the task brief marks
it optional/hard for the test-author. The "not triaged/failed → 409" conflict pinned below
(`test_pending_alert_is_409_conflict_and_never_enqueues`) is a different, and load-bearing, 409
path: the pre-lock status check, not lock contention.
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
from core.models import AlertRow, AlertStatus
from tests.helpers import seed_alert

ADMIN_TOKEN = "test-admin-token-2f8c"  # noqa: S105 -- fixture literal, not a real secret


@dataclass
class FakeEnqueue:
    """Records every `alert_id` it is invoked with — the fake of the external queue seam
    (`api.deps.EnqueueFn`), never our own code (mirrors `tests/test_ingest.py::FakeEnqueue`)."""

    calls: list[uuid.UUID] = field(default_factory=list)

    async def __call__(self, alert_id: uuid.UUID) -> None:
        self.calls.append(alert_id)


def _settings(**overrides: object) -> Settings:
    return Settings(admin_token=SecretStr(ADMIN_TOKEN), **overrides)


def _auth(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed(
    session_factory: async_sessionmaker[AsyncSession], *, session_id: str, status: AlertStatus
) -> uuid.UUID:
    async with session_factory() as session:
        alert_id = await seed_alert(session, session_id=session_id, status=status)
        await session.commit()
        return alert_id


async def test_no_authorization_header_401(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=_settings(), redis=arq_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/admin/retriage/{uuid.uuid4()}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_wrong_bearer_token_401(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    app = create_app(session_factory=db_session_factory, settings=_settings(), redis=arq_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/admin/retriage/{uuid.uuid4()}", headers=_auth("wrong-token")
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_empty_admin_token_configured_is_always_401(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """`ADMIN_TOKEN` unset (the `Settings` default, `SecretStr("")`) must 401 even a caller who
    sends an empty bearer — never fail open just because both sides compare equal-empty."""
    app = create_app(session_factory=db_session_factory, settings=Settings(), redis=arq_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty_bearer = await client.post(
            f"/api/v1/admin/retriage/{uuid.uuid4()}", headers=_auth("")
        )
        real_looking = await client.post(
            f"/api/v1/admin/retriage/{uuid.uuid4()}", headers=_auth("anything")
        )

    assert empty_bearer.status_code == 401
    assert real_looking.status_code == 401


async def test_valid_bearer_on_triaged_alert_flips_to_pending_and_enqueues(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    fake_enqueue = FakeEnqueue()
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(),
        enqueue=fake_enqueue,
        redis=arq_redis,
    )
    alert_id = await _seed(db_session_factory, session_id="retriage-triaged", status="triaged")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/admin/retriage/{alert_id}", headers=_auth())

    assert response.status_code == 202
    assert response.json() == {"id": str(alert_id), "status": "pending", "retriaged": True}
    assert fake_enqueue.calls == [alert_id]

    async with db_session_factory() as session:
        row = await session.get(AlertRow, alert_id)
    assert row is not None
    assert row.status == "pending"


async def test_valid_bearer_on_failed_alert_flips_to_pending_and_enqueues(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    fake_enqueue = FakeEnqueue()
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(),
        enqueue=fake_enqueue,
        redis=arq_redis,
    )
    alert_id = await _seed(db_session_factory, session_id="retriage-failed", status="failed")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/admin/retriage/{alert_id}", headers=_auth())

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert fake_enqueue.calls == [alert_id]


async def test_pending_alert_is_409_conflict_and_never_enqueues(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    fake_enqueue = FakeEnqueue()
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(),
        enqueue=fake_enqueue,
        redis=arq_redis,
    )
    alert_id = await _seed(db_session_factory, session_id="retriage-pending", status="pending")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/admin/retriage/{alert_id}", headers=_auth())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert fake_enqueue.calls == []

    async with db_session_factory() as session:
        row = await session.get(AlertRow, alert_id)
    assert row is not None
    assert row.status == "pending"


async def test_daily_cap_429_before_any_db_work(
    db_session_factory: async_sessionmaker[AsyncSession],
    arq_redis: ArqRedis,
) -> None:
    """`RETRIAGE_PER_DAY=1` here — never the production default of 20 — purely to keep the test
    cheap; the mechanism pinned (a single global Redis counter, checked before the alert lookup)
    is identical at any cap value. The second call names a UUID that was never inserted: a `404`
    would mean the DB was reached before the cap check; `429` proves it was not.
    """
    fake_enqueue = FakeEnqueue()
    app = create_app(
        session_factory=db_session_factory,
        settings=_settings(retriage_per_day=1),
        enqueue=fake_enqueue,
        redis=arq_redis,
    )
    alert_id = await _seed(db_session_factory, session_id="retriage-cap-1", status="triaged")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(f"/api/v1/admin/retriage/{alert_id}", headers=_auth())
        second = await client.post(f"/api/v1/admin/retriage/{uuid.uuid4()}", headers=_auth())

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert fake_enqueue.calls == [alert_id]
