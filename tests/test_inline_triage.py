"""Pins the M2 inline-triage wiring end to end (PRD §6.2, §6.5, §12 M2) — m2 task-04.

A signed POST through the *real* app (a real `TriagePipeline` wired as `triage=...`, with only
the LLM faked) must produce exactly one `alerts` row and, on success, exactly one `verdicts` row
in the same request; a validation or LLM-call failure must mark the alert `failed` and leave no
verdict row; duplicates must never invoke the LLM again. `TriagePipeline.triage_alert` is also
pinned directly (not just through the route), including that it owns its own commit.

`test_api_routes_never_import_worker` pins CONVENTIONS.md §2 contract 3's documented exception:
only `api.main` may import `worker` for M2's inline wiring; `api.factory` and
`api.routes.alerts` — the modules the route layer actually depends on — must never pull `worker`
into `sys.modules`, in-process or transitively. It runs in a fresh subprocess so a `worker` import
anywhere else in the test session cannot mask a real regression.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from core.errors import LLMCallError, NotFoundError
from core.models import AlertRow, VerdictRow
from core.schemas.alert import SessionAlert
from core.services.alerts import insert_alert
from core.signing import SIGNATURE_HEADER, sign_body
from tests.fakes import FakeLLMClient
from worker.triage import TriagePipeline

_FIXTURE_BODY = (
    Path(__file__).resolve().parent.parent / "fixtures" / "alerts" / "alert4.json"
).read_bytes()
_SECRET = "test-secret"  # matches the `settings` fixture's `ingest_hmac_secret`

_VALID_VERDICT_JSON = json.dumps(
    {
        "severity": 4,
        "category": "successful_intrusion",
        "confidence": 0.9,
        "reasoning": "attacker logged in as root and ran reconnaissance commands",
        "recommended_action": "isolate host and rotate credentials",
        "escalate": True,
    }
)


def _signed_headers(body: bytes, *, secret: str = _SECRET) -> dict[str, str]:
    return {SIGNATURE_HEADER: sign_body(secret, body), "content-type": "application/json"}


def _load_alert(**overrides: object) -> SessionAlert:
    data = json.loads(_FIXTURE_BODY)
    data.update(overrides)
    return SessionAlert.model_validate(data)


def _build_app(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, llm: FakeLLMClient
) -> FastAPI:
    """A real `create_app()` wired with a real `TriagePipeline` over a faked LLM."""
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")
    return create_app(
        session_factory=session_factory, settings=settings, triage=pipeline.triage_alert
    )


async def _count_alerts(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(AlertRow))
    assert count is not None
    return count


async def _count_verdicts(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(VerdictRow))
    assert count is not None
    return count


async def test_signed_post_creates_alert_and_verdict_rows(
    db_session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    llm = FakeLLMClient([_VALID_VERDICT_JSON])
    app = _build_app(db_session_factory, settings, llm)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=_FIXTURE_BODY, headers=_signed_headers(_FIXTURE_BODY)
        )

    assert response.status_code == 202
    assert await _count_alerts(db_session_factory) == 1
    assert await _count_verdicts(db_session_factory) == 1

    async with db_session_factory() as session:
        verdict = (await session.execute(select(VerdictRow))).scalar_one()
    assert verdict.prompt_version == "triage-v1"
    assert verdict.model_primary == "fake-model"


async def test_response_reports_triaged_status(
    db_session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    llm = FakeLLMClient([_VALID_VERDICT_JSON])
    app = _build_app(db_session_factory, settings, llm)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=_FIXTURE_BODY, headers=_signed_headers(_FIXTURE_BODY)
        )

    assert response.json()["status"] == "triaged"

    async with db_session_factory() as session:
        row = await session.get(AlertRow, uuid.UUID(response.json()["id"]))
    assert row is not None
    assert row.status == "triaged"


async def test_second_validation_failure_marks_failed_returns_202(
    db_session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    llm = FakeLLMClient(["{}", "{}"])
    app = _build_app(db_session_factory, settings, llm)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=_FIXTURE_BODY, headers=_signed_headers(_FIXTURE_BODY)
        )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert await _count_verdicts(db_session_factory) == 0

    async with db_session_factory() as session:
        row = await session.get(AlertRow, uuid.UUID(response.json()["id"]))
    assert row is not None
    assert row.status == "failed"


async def test_llm_call_error_marks_failed(
    db_session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    llm = FakeLLMClient([LLMCallError("boom")])
    app = _build_app(db_session_factory, settings, llm)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/alerts", content=_FIXTURE_BODY, headers=_signed_headers(_FIXTURE_BODY)
        )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert await _count_verdicts(db_session_factory) == 0

    async with db_session_factory() as session:
        row = await session.get(AlertRow, uuid.UUID(response.json()["id"]))
    assert row is not None
    assert row.status == "failed"


async def test_duplicate_post_leaves_verdict_count_unchanged(
    db_session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    llm = FakeLLMClient([_VALID_VERDICT_JSON])
    app = _build_app(db_session_factory, settings, llm)
    headers = _signed_headers(_FIXTURE_BODY)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)
        second = await client.post("/api/v1/alerts", content=_FIXTURE_BODY, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert len(llm.calls) == 1
    assert await _count_verdicts(db_session_factory) == 1


async def test_triage_alert_direct_returns_triaged_and_commits(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert = _load_alert(session_id="direct-triage-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    llm = FakeLLMClient([_VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")

    status = await pipeline.triage_alert(db_session, result.alert_id)

    assert status == "triaged"

    # A fresh session (not `db_session`) must already see the verdict: `triage_alert` commits.
    async with db_session_factory() as fresh_session:
        verdict = (
            await fresh_session.execute(
                select(VerdictRow).where(VerdictRow.alert_id == result.alert_id)
            )
        ).scalar_one()
    assert verdict.model_primary == "fake-model"
    assert verdict.prompt_version == "triage-v1"


async def test_triage_alert_direct_failure_commits_failed_status(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`triage_alert`'s failure-path `commit()` (`worker/triage.py:166`) is load-bearing on its
    own — not merely masked by `get_session`'s dependency-level commit when called through the
    route. Fix round 1 (m2 task-04, review I2)."""
    alert = _load_alert(session_id="direct-failure-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    llm = FakeLLMClient([LLMCallError("boom")])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")

    status = await pipeline.triage_alert(db_session, result.alert_id)

    assert status == "failed"

    # A fresh session (not `db_session`) must already read "failed" and see no verdict row:
    # `triage_alert` commits the failure-path status write itself.
    async with db_session_factory() as fresh_session:
        row = await fresh_session.get(AlertRow, result.alert_id)
        assert row is not None
        assert row.status == "failed"
        verdict_count = await fresh_session.scalar(
            select(func.count())
            .select_from(VerdictRow)
            .where(VerdictRow.alert_id == result.alert_id)
        )
    assert verdict_count == 0


async def test_triage_alert_failure_rolls_back_dirty_session(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`triage_alert`'s failure-path `rollback()` (`worker/triage.py:164`) is load-bearing: any
    other uncommitted write sitting in the same session when triage fails must be discarded, not
    just the (already-clean) `get_alert` load. Fix round 1 (m2 task-04, review I3)."""
    alert_a = _load_alert(session_id="rollback-a-001")
    result_a = await insert_alert(db_session, alert_a)
    await db_session.commit()

    # A second, still-uncommitted write in the *same* session: the failure path's rollback must
    # discard this dirty row along with anything else pending, leaving only A's own
    # already-committed row (now `failed`) behind.
    alert_b = _load_alert(session_id="rollback-b-002")
    db_session.add(
        AlertRow(
            fingerprint=alert_b.fingerprint(),
            source=alert_b.source,
            event_time=alert_b.connect_time,
            raw=alert_b.model_dump(mode="json"),
        )
    )
    await db_session.flush()

    llm = FakeLLMClient(["{}", "{}"])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")

    status = await pipeline.triage_alert(db_session, result_a.alert_id)

    assert status == "failed"

    async with db_session_factory() as fresh_session:
        row_a = await fresh_session.get(AlertRow, result_a.alert_id)
        assert row_a is not None
        assert row_a.status == "failed"

        row_b = (
            await fresh_session.execute(
                select(AlertRow).where(AlertRow.fingerprint == alert_b.fingerprint())
            )
        ).scalar_one_or_none()
    assert row_b is None


async def test_triage_alert_unknown_alert_raises_not_found(db_session: AsyncSession) -> None:
    llm = FakeLLMClient([_VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")

    with pytest.raises(NotFoundError):
        await pipeline.triage_alert(db_session, uuid.uuid4())


def test_api_routes_never_import_worker() -> None:
    """Only `api.main` carries the M2 `ignore_imports` exception (CONVENTIONS.md §2 contract 3).

    Runs in a subprocess, importing exactly the modules the route layer depends on
    (`api.factory`, `api.routes.alerts`) and nothing else, so a `worker` import anywhere else in
    the test session can never mask a regression here.
    """
    script = (
        "import importlib, sys\n"
        "importlib.import_module('api.factory')\n"
        "importlib.import_module('api.routes.alerts')\n"
        "leaked = sorted(m for m in sys.modules if m == 'worker' or m.startswith('worker.'))\n"
        "assert not leaked, leaked\n"
    )
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(repo_root)
    )
    assert result.returncode == 0, result.stderr
