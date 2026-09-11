"""New file (m5 fix wave, review N-M1): `TriagePipeline.triage_attempt`'s own rollback on
`asyncio.CancelledError` is unpinned at HEAD — mutant M12 (`except BaseException` narrowed to
`except Exception`) survives all 28 tests in `tests/test_triage_alert.py`/
`tests/test_worker_job_retry.py`, because the job-level cancellation test
(`test_cancellation_propagates_and_is_never_turned_into_failed`) only observes the SESSION's own
context-manager rollback on close, never `triage_attempt`'s own `except BaseException: await
session.rollback()`.

This file calls `triage_attempt` directly against a `db_session` the CALLER keeps open across the
call — the shape `scripts/seed_dev.py`'s `triage_alert` and M8's admin retriage route both use, and
the only shape under which the attempt's own rollback (not a session close) is the sole thing that
can release the row lock (`worker/triage.py::triage_attempt`'s own docstring: "this method holds
that lock for the WHOLE attempt ... ANY failure (including cancellation) rolls that write back").
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.services.alerts import get_alert_for_update
from tests.helpers import seed_alert
from worker.triage import TriagePipeline


class _CancellingLLMClient:
    """Raises `asyncio.CancelledError` from `complete_structured` — the one thing
    `tests.fakes.FakeLLMClient` cannot script (it only raises `Exception` instances). Duplicated
    from `tests/test_worker_job_retry.py::_CancellingLLMClient` per the repo's own convention that
    test files never import from each other."""

    async def complete_structured(self, *, messages, response_model, model):
        raise asyncio.CancelledError()


async def test_triage_attempt_rolls_back_and_releases_the_lock_on_cancellation(
    db_session: AsyncSession, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """m5 fix wave (review N-M1, mutant M12): a cancellation mid-attempt must still hit
    `triage_attempt`'s own `except BaseException` rollback — proven here on a session the caller
    keeps open across the call, so nothing but that rollback can release the row lock."""
    alert_id = await seed_alert(db_session, "alert4", session_id="cancel-attempt-direct")
    await db_session.commit()

    pipeline = TriagePipeline(
        llm=_CancellingLLMClient(), model="fake-model", prompt_version="triage-v1"
    )

    with pytest.raises(asyncio.CancelledError):
        await pipeline.triage_attempt(db_session, alert_id)

    assert not db_session.in_transaction()

    # The rollback released the row lock: a fresh session's own `get_alert_for_update` must
    # return promptly, not hang behind the cancelled attempt's lock.
    async with db_session_factory() as fresh_session:
        locked_row = await asyncio.wait_for(
            get_alert_for_update(fresh_session, alert_id), timeout=2.0
        )
        assert locked_row.id == alert_id
        await fresh_session.commit()
