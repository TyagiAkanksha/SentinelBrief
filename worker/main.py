"""The ARQ entrypoint (CONVENTIONS.md §5) — `arq worker.main.WorkerSettings` runs this.

The only process that ever builds an LLM client or a `TriagePipeline` (PRD §10.1; spine M5-b):
the `LLM_API_KEY`/`CHEAP_MODEL`/unpriced-model fail-fast pins that used to live on `api.main`
(m2 task-04) move here. `startup`/`shutdown` own the process-lifetime `httpx.AsyncClient` and the
DB engine (M4 task-06 fix-1 I4), closing both idempotently on shutdown. Nothing imports this
module (import-linter contract 5) — it is a process entrypoint, not a library.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings, require_nonempty
from core.db import make_engine, make_session_factory
from core.queue import TRIAGE_JOB_NAME, TRIAGE_QUEUE_NAME, redis_settings
from worker.jobs import triage_alert_job
from worker.llm_client import OpenAICompatibleLLMClient
from worker.triage import TriagePipeline

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure stdlib logging at INFO for the running process (mirrors `api/main.py`)."""
    logging.basicConfig(level=logging.INFO)


_configure_logging()

settings: Settings = Settings()
require_nonempty("DATABASE_URL", settings.database_url.get_secret_value())
require_nonempty("REDIS_URL", settings.redis_url.get_secret_value())
require_nonempty("LLM_API_KEY", settings.llm_api_key.get_secret_value())
require_nonempty("CHEAP_MODEL", settings.cheap_model)


async def startup(ctx: dict[str, Any]) -> None:
    """Build the engine, session factory, `httpx` client and `TriagePipeline` for this process.

    Neither the DB engine nor the `httpx` client connects eagerly, so this never makes a real
    network call — it is safe to run against unreachable hosts in tests.

    Args:
        ctx: The ARQ job context; must carry `"settings"` (installed by `WorkerSettings.ctx`).

    Raises:
        ConfigError: `s.cheap_model` (or a non-empty `s.strong_model`) has no entry in
            `s.model_prices_json` (`OpenAICompatibleLLMClient.from_settings`).
    """
    s: Settings = ctx["settings"]
    engine: AsyncEngine = make_engine(s.database_url.get_secret_value())
    ctx["engine"] = engine
    ctx["session_factory"] = make_session_factory(engine)
    ctx["http"] = httpx.AsyncClient(timeout=s.abuseipdb_timeout_s)

    llm = OpenAICompatibleLLMClient.from_settings(s)
    ctx["pipeline"] = TriagePipeline.from_settings(s, llm=llm, http=ctx["http"])
    logger.info(
        "worker ready model=%s prompt=%s tools=%s",
        s.cheap_model,
        s.triage_prompt_version,
        ctx["pipeline"].tool_names,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """Close the process-lifetime `httpx` client and dispose the DB engine, idempotently.

    Args:
        ctx: The ARQ job context `startup` populated.
    """
    http = ctx.pop("http", None)
    if http is not None:
        await http.aclose()
    engine = ctx.pop("engine", None)
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    """ARQ's own config surface (`arq worker.main.WorkerSettings`); every value traces back to
    `Settings`/env — never a literal (m5 task-01 brief, Interfaces block)."""

    functions = [func(triage_alert_job, name=TRIAGE_JOB_NAME)]
    queue_name = TRIAGE_QUEUE_NAME
    redis_settings: RedisSettings = redis_settings(settings.redis_url.get_secret_value())
    on_startup = startup
    on_shutdown = shutdown
    ctx: dict[str, Any] = {"settings": settings}
    job_timeout = settings.triage_job_timeout_s
    max_jobs = settings.worker_max_jobs
    health_check_interval = settings.worker_health_check_interval_s
    retry_jobs = True
    # max_tries is set by task-02 (from TRIAGE_JOB_MAX_TRIES); ARQ's default (5) stands until then.
