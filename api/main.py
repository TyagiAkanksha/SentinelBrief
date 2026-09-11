"""Wiring entrypoint (CONVENTIONS.md §5): settings, engine, session factory, `create_app()`.

The only module that reads real secrets and fails fast on empty required ones. Nothing imports
this module (import-linter contract 5) — it is a process entrypoint, not a library.

`api/` never imports `worker` or `core.llm` (import-linter contract 3, PRD §10.1) — the M2 inline
triage `ignore_imports` exception is removed as of m5 task-01; the ARQ job (`worker/jobs.py`) owns
every LLM call now. The route layer never sees more than an `EnqueueFn` callable (`api/deps.py`).
"""

from __future__ import annotations

import logging
import uuid

from arq.connections import ArqRedis
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings, require_nonempty
from core.db import make_engine, make_session_factory
from core.queue import enqueue_triage, make_redis


def _configure_logging() -> None:
    """Configure stdlib logging at INFO for the running process."""
    logging.basicConfig(level=logging.INFO)


_configure_logging()

settings: Settings = Settings()
require_nonempty("DATABASE_URL", settings.database_url.get_secret_value())
require_nonempty("INGEST_HMAC_SECRET", settings.ingest_hmac_secret.get_secret_value())
require_nonempty("REDIS_URL", settings.redis_url.get_secret_value())

engine: AsyncEngine = make_engine(settings.database_url.get_secret_value())
session_factory: async_sessionmaker[AsyncSession] = make_session_factory(engine)

redis_client: ArqRedis = make_redis(
    settings.redis_url.get_secret_value(), socket_timeout_s=settings.redis_socket_timeout_s
)


async def enqueue(alert_id: uuid.UUID) -> None:
    """Enqueue one `triage_alert` job for `alert_id` on the wired `redis_client`."""
    await enqueue_triage(redis_client, alert_id)


# No `cache=` kwarg (m5 task-05 fix-1, review I2): a public GET route must never be able to grow
# a Redis instance that also holds the ARQ queue (PRD §10.1) — `create_app()`'s own bounded
# `InMemoryTTLCache` default stays the production list/stats cache.
app: FastAPI = create_app(
    session_factory=session_factory,
    settings=settings,
    enqueue=enqueue,
    redis=redis_client,
)
