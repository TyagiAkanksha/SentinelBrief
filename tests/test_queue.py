"""Pins `core/queue.py` — the one module that knows ARQ's job name, queue name and job id
(PRD §3, §4, §6.1 step 3; m5 task-01 brief, Interfaces block).

`enqueue_triage` queues exactly one job per alert id (`triage:<alert_id>`, on
`sentinelbrief:triage`), is idempotent per id (ARQ refuses a second job with the same id: queued,
running, or its result still kept), and turns a dead Redis into `QueueUnavailableError` without
ever echoing the connection URL, host, or password on the wire or into a log line — the one
seam every duplicate-POST and Redis-outage story in `tests/test_ingest.py` depends on.
`make_redis`/`redis_settings` are the sync, lazy-connect client/settings factories both `api/`
and `worker/` build their Redis seam from at module-import time (no event loop needed).

Every Redis-touching test here uses the `arq_redis` fixture (flushed before and after, against
the DEDICATED test Redis on 6380 — never the dev compose Redis on 6379).
"""

from __future__ import annotations

import logging
import uuid

import pytest
from arq.connections import ArqRedis
from arq.jobs import Job
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from api.errors import GENERIC_MESSAGE
from api.factory import create_app
from core.config import Settings
from core.errors import QueueUnavailableError
from core.queue import (
    TRIAGE_JOB_NAME,
    TRIAGE_QUEUE_NAME,
    enqueue_triage,
    make_redis,
    triage_job_id,
)


async def test_enqueue_triage_queues_one_job_with_the_alert_job_id(arq_redis: ArqRedis) -> None:
    alert_id = uuid.uuid4()

    queued = await enqueue_triage(arq_redis, alert_id)

    assert queued is True
    assert await arq_redis.zscore(TRIAGE_QUEUE_NAME, triage_job_id(alert_id)) is not None

    job = Job(triage_job_id(alert_id), arq_redis, _queue_name=TRIAGE_QUEUE_NAME)
    info = await job.info()
    assert info is not None
    assert info.function == TRIAGE_JOB_NAME
    assert info.args == (str(alert_id),)


async def test_enqueue_triage_is_idempotent_per_alert_id(arq_redis: ArqRedis) -> None:
    alert_id = uuid.uuid4()

    first = await enqueue_triage(arq_redis, alert_id)
    second = await enqueue_triage(arq_redis, alert_id)

    assert first is True
    assert second is False
    assert await arq_redis.zcard(TRIAGE_QUEUE_NAME) == 1


async def test_enqueue_triage_dead_redis_is_queue_unavailable_and_never_leaks_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Port 1 refuses immediately (no listener there); a distinct password per branch (rule 5)
    # so a leak can only be this test's own secret, never one borrowed from another test.
    dead = make_redis("redis://:queue-pw-1@127.0.0.1:1/0", socket_timeout_s=0.5)
    alert_id = uuid.uuid4()

    try:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(QueueUnavailableError) as exc_info:
                await enqueue_triage(dead, alert_id)

        assert "queue-pw-1" not in str(exc_info.value)
        assert "127.0.0.1" not in str(exc_info.value)
        assert "queue-pw-1" not in caplog.text
    finally:
        await dead.aclose()


def test_make_redis_applies_the_socket_timeouts_and_queue_name() -> None:
    client = make_redis("redis://127.0.0.1:6399/2", socket_timeout_s=2.5)

    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_timeout"] == 2.5
    assert kwargs["socket_connect_timeout"] == 2.5
    # A distinguishable value: arq's own built-in default queue name is "arq:queue", never this
    # constant, so this assertion can only pass if `make_redis` actually threads it through.
    assert client.default_queue_name == TRIAGE_QUEUE_NAME


def test_triage_job_id_shape() -> None:
    alert_id = uuid.uuid4()

    assert triage_job_id(alert_id) == f"triage:{alert_id}"


async def test_queue_unavailable_maps_to_503_envelope() -> None:
    app = create_app()

    @app.get("/__test_probe_queue_unavailable__")
    async def _probe() -> None:
        raise QueueUnavailableError("x")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/__test_probe_queue_unavailable__")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "queue_unavailable"
    assert body["error"]["message"] == GENERIC_MESSAGE
    assert body["error"]["message"] != "x"


def test_queue_settings_defaults_bounds_and_secret_repr() -> None:
    # R17: a `Settings()`-vs-`Settings()` comparison is tautological — these four literals are
    # the brief's own Interfaces-block defaults, not read back from a fresh Settings() instance.
    settings = Settings()
    assert settings.redis_socket_timeout_s == 2.0
    assert settings.triage_job_timeout_s == 120
    assert settings.worker_max_jobs == 4
    assert settings.worker_health_check_interval_s == 15

    with pytest.raises(ValidationError):
        Settings(redis_socket_timeout_s=0)
    with pytest.raises(ValidationError):
        Settings(worker_max_jobs=0)
    with pytest.raises(ValidationError):
        Settings(triage_job_timeout_s=0)

    secret_settings = Settings(redis_url=SecretStr("redis://:repr-pw-2@h/0"))
    assert "repr-pw-2" not in repr(secret_settings)
