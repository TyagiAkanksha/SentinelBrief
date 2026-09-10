"""Pins `worker/publish.py` — the `verdict.created` Redis pub/sub publish, best-effort after a
triage commit (PRD §8 `/stream` row, §6.2; m5 task-02 brief, Interfaces block).

`publish_verdict_created` NEVER raises: the verdict is already durable by the time it is called
(`worker/jobs.py` calls it only after a successful `triage_attempt`), so a dead Redis is a
WARNING, never a failed job. `tests/test_worker_job_retry.py`'s own
`test_publish_failure_does_not_fail_the_job` pins the same guarantee end to end through
`triage_alert_job` directly (a real `Worker` cannot have its `ctx["redis"]` swapped for a raising
fake — ARQ installs that key itself).
"""

from __future__ import annotations

import logging
import uuid

import pytest
import redis.exceptions

from core.schemas.alerts_read import REASONING_EXCERPT_CHARS, reasoning_excerpt
from core.schemas.verdict import Verdict
from worker.publish import publish_verdict_created, verdict_created_payload


def _verdict(reasoning: str | None = None) -> Verdict:
    """A severity-4, `successful_intrusion`, escalate=true verdict with a long reasoning string
    (> `REASONING_EXCERPT_CHARS`) so the payload-shape test can prove the excerpt actually
    truncates rather than merely echoing a short string unchanged."""
    return Verdict(
        severity=4,
        category="successful_intrusion",
        confidence=0.9,
        reasoning=reasoning or ("attacker logged in as root and ran reconnaissance commands. " * 5),
        recommended_action="isolate host and rotate credentials",
        escalate=True,
    )


def test_verdict_created_payload_shape() -> None:
    alert_id = uuid.uuid4()
    verdict_id = uuid.uuid4()
    verdict = _verdict()

    payload = verdict_created_payload(alert_id=alert_id, verdict_id=verdict_id, verdict=verdict)

    assert set(payload.keys()) == {
        "alert_id",
        "verdict_id",
        "severity",
        "category",
        "escalate",
        "summary",
    }
    assert payload["alert_id"] == str(alert_id)
    assert payload["verdict_id"] == str(verdict_id)
    assert isinstance(payload["alert_id"], str)
    assert isinstance(payload["verdict_id"], str)
    assert payload["severity"] == 4
    assert payload["category"] == "successful_intrusion"
    assert payload["escalate"] is True
    assert payload["summary"] == reasoning_excerpt(verdict.reasoning)
    assert payload["summary"] == verdict.reasoning[:REASONING_EXCERPT_CHARS]
    assert len(payload["summary"]) == REASONING_EXCERPT_CHARS
    assert len(payload["summary"]) < len(verdict.reasoning)


class _RaisingRedis:
    """A fake Redis whose `publish` always raises `redis.exceptions.ConnectionError` (m5 task-02
    brief, resolution 7) — the one external seam this module touches."""

    async def publish(self, channel: str, message: str) -> int:
        raise redis.exceptions.ConnectionError("down")


async def test_publish_failure_is_a_warning_not_a_raise(caplog: pytest.LogCaptureFixture) -> None:
    alert_id = uuid.uuid4()
    verdict_id = uuid.uuid4()
    verdict = _verdict()

    with caplog.at_level(logging.WARNING):
        result = await publish_verdict_created(
            _RaisingRedis(), alert_id=alert_id, verdict_id=verdict_id, verdict=verdict
        )

    assert result is False

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(alert_id) in message
    assert str(verdict_id) in message
    assert "exc=ConnectionError" in message
    # Never leak the exception's own message text (CONVENTIONS.md: log ids/counts/class names).
    assert "down" not in message
