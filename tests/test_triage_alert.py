"""Pins `TriagePipeline.triage_alert` directly — load, run, persist as one transaction
(PRD §6.2, §6.5) — m2 task-04; renamed and trimmed at m5 task-01 (queue split).

`api/routes/alerts.py` no longer calls `TriagePipeline.triage_alert` inline: the M2 HTTP-level
pins that drove the route through a real `TriagePipeline` (`_build_app`, the five
`test_signed_post_*`/`test_duplicate_post_*` tests) are retired here, because that code path no
longer exists — `POST /api/v1/alerts` only inserts, commits and enqueues
(`tests/test_ingest.py`); a worker-side burst-`Worker` pin of the same success/failure/missing
outcomes now lives in `tests/test_worker_job.py`. What remains is `triage_alert` itself, called
directly against a real, already-committed alert row: it must own its own commit on success (a
fresh session, not the one that ran it, must already see the verdict), roll back and commit a
`failed` status in its own transaction on a validation/LLM-call failure (discarding any other
dirty write sitting in the same session), and raise `NotFoundError` for an alert id that does not
exist.

`test_api_routes_never_import_worker` (the contract-3 exception pin) moves to
`tests/test_api_main.py::test_api_main_never_imports_worker_or_core_llm`: the exception itself
moved from `api.main`'s M2 inline-triage wiring to nothing (m5 task-01 deletes the
`ignore_imports` lines outright), so the pin now asserts the entrypoint pulls in neither `worker`
nor `core.llm` at all — not just that the route layer stays clean.

m5 task-02 adds two direct pins of the new `TriagePipeline.triage_attempt` (PRD §6.2, Interfaces
block): the load-run-persist-and-commit body that used to be inline in `triage_alert` and is now
`triage_alert`'s own building block (`triage_alert` re-expressed over it stays covered by the four
pins above, unchanged). Unlike `triage_alert`, a failing `triage_attempt` never marks the alert
`failed` itself — it rolls back and RAISES, because deciding retry-vs-fail from there is the job's
job (`worker/jobs.py`, `worker/retry.py`), not the attempt's.

m5 task-03 (PRD §6.4, §5) adds one more direct pin: `persist_verdict`'s `model_primary` /
`model_final` (= `VerdictRow.model_final`, `outcome.model`) / `escalated_model` columns actually
land on the row when `triage_attempt` runs a pipeline with routing turned on, both when it
escalates and when it doesn't.

m5 task-04 (PRD §6.1 idempotency, §12 M5) adds the lock/skip pins: `triage_attempt` now loads the
alert through `get_alert_for_update` and holds that lock for the whole attempt, so
`test_triage_attempt_returns_verdict_id_and_commits` gains a follow-up lock-release assertion, a
new `test_triage_attempt_skips_an_already_triaged_alert_without_an_llm_call` pins the skip
branch (both terminal statuses, plus the `triage_alert` "skipped" -> "triaged" mapping), and
`test_no_pending_orm_state_before_the_tool_loop` re-proves the M4 task-05 "clean identity map"
guarantee now that the loaded row also carries a lock.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.errors import LLMCallError, NotFoundError, VerdictValidationError
from core.models import AlertRow, VerdictRow
from core.schemas.verdict import Verdict
from core.services.alerts import get_alert_for_update, insert_alert
from tests.fakes import FakeLLMClient, ScriptedToolCall
from tests.helpers import VALID1, VALID4, VALID4_STRONG, load_alert, make_registry, seed_alert
from worker.tools import ToolContext
from worker.triage import AttemptResult, TriagePipeline

_VALID_VERDICT_JSON = (
    '{"severity": 4, "category": "successful_intrusion", "confidence": 0.9, '
    '"reasoning": "attacker logged in as root and ran reconnaissance commands", '
    '"recommended_action": "isolate host and rotate credentials", "escalate": true}'
)


async def test_triage_alert_direct_returns_triaged_and_commits(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert = load_alert(session_id="direct-triage-001")
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
    alert = load_alert(session_id="direct-failure-001")
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
    alert_a = load_alert(session_id="rollback-a-001")
    result_a = await insert_alert(db_session, alert_a)
    await db_session.commit()

    # A second, still-uncommitted write in the *same* session: the failure path's rollback must
    # discard this dirty row along with anything else pending, leaving only A's own
    # already-committed row (now `failed`) behind.
    alert_b = load_alert(session_id="rollback-b-002")
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


async def test_triage_attempt_returns_verdict_id_and_commits(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert = load_alert(session_id="attempt-commits-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    llm = FakeLLMClient([_VALID_VERDICT_JSON])
    pipeline = TriagePipeline(llm=llm, model="fake-model", prompt_version="triage-v1")

    attempt = await pipeline.triage_attempt(db_session, result.alert_id)

    assert attempt.status == "triaged"
    assert attempt.verdict_id is not None
    assert attempt.outcome is not None

    # A fresh session (not `db_session`) must already see the verdict: `triage_attempt` commits.
    async with db_session_factory() as fresh_session:
        verdict = (
            await fresh_session.execute(
                select(VerdictRow).where(VerdictRow.alert_id == result.alert_id)
            )
        ).scalar_one()
    assert verdict.id == attempt.verdict_id
    assert verdict.model_primary == "fake-model"
    assert verdict.prompt_version == "triage-v1"

    # The row lock is released with the attempt's own commit (m5 task-04): a fresh session's
    # `get_alert_for_update` must return promptly, not hang behind a lock triage_attempt forgot
    # to release.
    async with db_session_factory() as lock_check_session:
        locked_row = await asyncio.wait_for(
            get_alert_for_update(lock_check_session, result.alert_id), timeout=2.0
        )
        assert locked_row.id == result.alert_id
        await lock_check_session.commit()


async def test_triage_attempt_rolls_back_and_raises_on_failure(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The M2 `rollback-b-002` pattern (`test_triage_alert_failure_rolls_back_dirty_session`
    above), replayed against `triage_attempt` directly: the attempt rolls back any dirty write in
    the same session on failure, same as `triage_alert` — but it RAISES rather than returning a
    `"failed"` status, and never writes the alert's status itself. Deciding retry-vs-fail (and
    making the terminal `failed` write) is the job's decision (`worker/jobs.py`,
    `worker/retry.py`), not the attempt's (PRD §6.2, m5 task-02).
    """
    alert_a = load_alert(session_id="attempt-rollback-a-001")
    result_a = await insert_alert(db_session, alert_a)
    await db_session.commit()

    # A second, still-uncommitted write in the *same* session: the failure path's rollback must
    # discard this dirty row along with anything else pending.
    alert_b = load_alert(session_id="attempt-rollback-b-002")
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

    with pytest.raises(VerdictValidationError):
        await pipeline.triage_attempt(db_session, result_a.alert_id)

    # The rollback is only observable from *this* session: a session that was never rolled back
    # still holds the dirty `attempt-rollback-b-002` row in its identity map / transaction.
    assert not db_session.in_transaction() or not db_session.new
    await db_session.commit()  # committing after a real rollback must persist nothing new
    async with db_session_factory() as after:
        assert (
            await after.execute(
                select(AlertRow).where(AlertRow.fingerprint == alert_b.fingerprint())
            )
        ).scalar_one_or_none() is None

    async with db_session_factory() as fresh_session:
        row_a = await fresh_session.get(AlertRow, result_a.alert_id)
        assert row_a is not None
        assert row_a.status == "pending"  # the attempt itself never marks the alert failed

        row_b = (
            await fresh_session.execute(
                select(AlertRow).where(AlertRow.fingerprint == alert_b.fingerprint())
            )
        ).scalar_one_or_none()
    assert row_b is None


async def test_escalated_attempt_persists_both_models(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Routing on, escalated: the persisted row carries the cheap id as `model_primary`, the
    strong id as `model_final`, and `escalated_model=True` — tokens summed across both tiers
    (PRD §6.4, §5)."""
    alert = load_alert(session_id="escalated-attempt-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    llm = FakeLLMClient([VALID4, VALID4_STRONG])
    pipeline = TriagePipeline(
        llm=llm,
        model="fake-model",
        prompt_version="triage-v1",
        strong_model="strong-model",
        escalate_severity_gte=4,
        escalate_confidence_lt=0.6,
    )

    attempt = await pipeline.triage_attempt(db_session, result.alert_id)

    assert attempt.status == "triaged"
    assert attempt.outcome is not None
    assert attempt.outcome.escalated_model is True

    async with db_session_factory() as fresh_session:
        verdict = (
            await fresh_session.execute(
                select(VerdictRow).where(VerdictRow.alert_id == result.alert_id)
            )
        ).scalar_one()
    assert verdict.model_primary == "fake-model"
    assert verdict.model_final == "strong-model"
    assert verdict.escalated_model is True
    assert verdict.input_tokens == 200  # 2 calls x 100 (cheap + strong)


async def test_non_escalated_attempt_persists_the_same_model_twice(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Routing on, NOT escalated (the cheap verdict clears neither threshold): the persisted row
    still carries `model_primary == model_final == the cheap id` and `escalated_model=False`,
    same as routing being off entirely (PRD §6.4)."""
    alert = load_alert(session_id="non-escalated-attempt-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    llm = FakeLLMClient([VALID1])
    pipeline = TriagePipeline(
        llm=llm,
        model="fake-model",
        prompt_version="triage-v1",
        strong_model="strong-model",
        escalate_severity_gte=4,
        escalate_confidence_lt=0.6,
    )

    attempt = await pipeline.triage_attempt(db_session, result.alert_id)

    assert attempt.status == "triaged"
    assert attempt.outcome is not None
    assert attempt.outcome.escalated_model is False

    async with db_session_factory() as fresh_session:
        verdict = (
            await fresh_session.execute(
                select(VerdictRow).where(VerdictRow.alert_id == result.alert_id)
            )
        ).scalar_one()
    assert verdict.model_primary == "fake-model"
    assert verdict.model_final == "fake-model"
    assert verdict.escalated_model is False


async def test_triage_attempt_skips_an_already_triaged_alert_without_an_llm_call(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """`AttemptResult` skip mapping (m5 task-04, PRD §6.1 idempotency, §12 M5 "kill the worker
    mid-job -> job re-runs, no duplicate verdicts"): once the locked row's status is no longer
    `pending`, the attempt releases the lock and returns before any LLM call, on EITHER terminal
    status (`triaged` or `failed`) -- a re-run after a crash that had already committed, or an
    overlapping run, can never write a second verdict row or spend a second set of tokens.
    `triage_alert` (the unchanged M2 contract) reports a `skipped` `AttemptResult` as `"triaged"`:
    the alert IS triaged, whichever run actually did the work.
    """
    triaged_id = await seed_alert(
        db_session,
        "alert4",
        verdict=Verdict.model_validate_json(VALID4),
        session_id="skip-triaged-001",
    )
    failed_id = await seed_alert(
        db_session, "alert4", status="failed", session_id="skip-failed-002"
    )
    await db_session.commit()

    fake = FakeLLMClient([])
    pipeline = TriagePipeline(llm=fake, model="fake-model", prompt_version="triage-v1")

    triaged_attempt = await pipeline.triage_attempt(db_session, triaged_id)
    assert triaged_attempt == AttemptResult(status="skipped", verdict_id=None, outcome=None)

    failed_attempt = await pipeline.triage_attempt(db_session, failed_id)
    assert failed_attempt == AttemptResult(status="skipped", verdict_id=None, outcome=None)

    assert len(fake.calls) == 0

    async with db_session_factory() as fresh_session:
        verdict_count = await fresh_session.scalar(
            select(func.count()).select_from(VerdictRow).where(VerdictRow.alert_id == triaged_id)
        )
    assert verdict_count == 1  # still exactly the one verdict seeded above, never a second

    status = await pipeline.triage_alert(db_session, triaged_id)
    assert status == "triaged"


class SessionSpyTool:
    """Records the triage session's ORM/transaction state the instant a tool executes (m5
    task-04): if the locked row came back dirty, or a leftover pending write sat in the identity
    map, `persist_verdict`'s own writes later in the same transaction could autoflush out of
    order. Local to this test only -- not one of the `tests/helpers.py` lifted stubs (m5 task-03).
    """

    name = "session_spy"
    description = "Records ctx.session state; always returns an empty result."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    external = False

    def __init__(self) -> None:
        self.dirty: set[Any] | None = None
        self.new: set[Any] | None = None
        self.in_transaction: bool | None = None

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        assert ctx.session is not None
        self.dirty = set(ctx.session.dirty)
        self.new = set(ctx.session.new)
        self.in_transaction = ctx.session.in_transaction()
        return {}


async def test_no_pending_orm_state_before_the_tool_loop(db_session: AsyncSession) -> None:
    alert = load_alert(session_id="no-dirty-orm-state-001")
    result = await insert_alert(db_session, alert)
    await db_session.commit()

    spy = SessionSpyTool()
    fake = FakeLLMClient([[ScriptedToolCall(name="session_spy", arguments={})], VALID1])
    pipeline = TriagePipeline(
        llm=fake,
        model="fake-model",
        prompt_version="triage-v1",
        tools=make_registry(spy),
        tool_loop_max_iter=6,
    )

    attempt = await pipeline.triage_attempt(db_session, result.alert_id)

    assert attempt.status == "triaged"
    assert spy.dirty == set()
    assert spy.new == set()
    assert spy.in_transaction is True
