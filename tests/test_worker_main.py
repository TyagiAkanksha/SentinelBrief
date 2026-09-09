"""Pins `worker/main.py`'s fail-fast wiring and its `WorkerSettings`/`startup`/`shutdown`
(CONVENTIONS.md §5; m5 task-01 brief, Interfaces block; spine M5-b).

`worker/main.py` is the ARQ entrypoint: module-level wiring fails fast on an empty required
secret, naming exactly which one (`DATABASE_URL`, `REDIS_URL`, `LLM_API_KEY`, `CHEAP_MODEL`, in
that order — `ConfigError` names the *first* empty one, mirroring `tests/test_api_main.py`'s
pattern one module over). The `LLM_API_KEY`/`CHEAP_MODEL`/unpriced-model pins that used to live on
`api.main` (m2 task-04) move here (spine M5-b): the worker is the only process that ever builds an
LLM client or a `TriagePipeline` now (PRD §10.1). `WorkerSettings` itself is read straight from a
`Settings` instance — never a literal — and `startup`/`shutdown` own the process-lifetime `httpx`
client and the DB engine, closing both (idempotently) in `shutdown` (M4 task-06 fix-1 I4).

Unlike every other new file in this task, `worker.main` is imported **inside** each test after
`monkeypatch` sets the env — never at module top — so a fresh `importlib.import_module` re-runs
its top-level wiring every time (mirrors `tests/test_api_main.py::_reset_api_main`).
"""

from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import ModelPrice, Settings
from core.errors import ConfigError
from core.queue import TRIAGE_JOB_NAME, TRIAGE_QUEUE_NAME
from worker.tools.wiring import TOOL_NAMES


def _reset_worker_main() -> None:
    """Drop any cached `worker.main` module so the next import re-runs its top-level wiring."""
    sys.modules.pop("worker.main", None)


def test_missing_database_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    _reset_worker_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("worker.main")
        assert "DATABASE_URL" in str(exc_info.value)
    finally:
        _reset_worker_main()


def test_missing_redis_url_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    _reset_worker_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("worker.main")
        assert "REDIS_URL" in str(exc_info.value)
    finally:
        _reset_worker_main()


def test_missing_llm_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    _reset_worker_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("worker.main")
        assert "LLM_API_KEY" in str(exc_info.value)
    finally:
        _reset_worker_main()


def test_missing_cheap_model_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _reset_worker_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("worker.main")
        assert "CHEAP_MODEL" in str(exc_info.value)
    finally:
        _reset_worker_main()


def test_worker_settings_are_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.delenv("TRIAGE_JOB_TIMEOUT_S", raising=False)
    monkeypatch.delenv("WORKER_MAX_JOBS", raising=False)
    monkeypatch.delenv("WORKER_HEALTH_CHECK_INTERVAL_S", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://:pw@127.0.0.1:6390/3")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    monkeypatch.setenv("TRIAGE_JOB_TIMEOUT_S", "77")
    monkeypatch.setenv("WORKER_MAX_JOBS", "3")
    monkeypatch.setenv("WORKER_HEALTH_CHECK_INTERVAL_S", "11")
    _reset_worker_main()

    try:
        module = importlib.import_module("worker.main")
        ws = module.WorkerSettings

        assert ws.redis_settings.host == "127.0.0.1"
        assert ws.redis_settings.port == 6390
        assert ws.redis_settings.database == 3
        assert ws.redis_settings.password == "pw"
        assert ws.job_timeout == 77
        assert ws.max_jobs == 3
        assert ws.health_check_interval == 11
        assert ws.queue_name == TRIAGE_QUEUE_NAME
        assert [f.name for f in ws.functions] == [TRIAGE_JOB_NAME]
        assert ws.on_startup is module.startup
        assert ws.on_shutdown is module.shutdown
        assert ws.retry_jobs is True
        assert ws.ctx["settings"].triage_job_timeout_s == 77
    finally:
        _reset_worker_main()


async def test_startup_builds_the_seams_and_shutdown_closes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    _reset_worker_main()

    try:
        module = importlib.import_module("worker.main")
        # Neither host resolves/accepts a real connection: `startup` must not need one (no
        # DB/Redis connection is made — SQLAlchemy/ArqRedis both connect lazily).
        settings = Settings(
            llm_api_key=SecretStr("sk-test"),
            cheap_model="fake-model",
            model_prices_json={
                "fake-model": ModelPrice(input_per_mtok=Decimal("0"), output_per_mtok=Decimal("0"))
            },
            database_url=SecretStr("postgresql://u:p@127.0.0.1:1/x"),
            abuseipdb_timeout_s=7.5,
        )
        ctx: dict[str, Any] = {"settings": settings}

        await module.startup(ctx)

        assert ctx["pipeline"].tool_names == TOOL_NAMES
        assert ctx["http"].timeout.read == 7.5
        assert isinstance(ctx["session_factory"], async_sessionmaker)

        http_client = ctx["http"]
        await module.shutdown(ctx)

        assert http_client.is_closed is True
        assert "http" not in ctx
        assert "engine" not in ctx

        await module.shutdown(ctx)  # second shutdown: no error (pop, not index)
    finally:
        _reset_worker_main()


async def test_startup_unpriced_cheap_model_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pin that leaves `tests/test_api_main.py` (spine M5-b): an unpriced `CHEAP_MODEL` is a
    boot-time `ConfigError`, raised from `startup` (`OpenAICompatibleLLMClient.from_settings`),
    never a silent zero cost (CONVENTIONS.md §7)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    _reset_worker_main()

    try:
        module = importlib.import_module("worker.main")
        settings = Settings(
            llm_api_key=SecretStr("sk-test"),
            cheap_model="fake-model",
            model_prices_json={},
            database_url=SecretStr("postgresql://u:p@127.0.0.1:1/x"),
        )
        ctx: dict[str, Any] = {"settings": settings}

        with pytest.raises(ConfigError) as exc_info:
            await module.startup(ctx)

        assert "fake-model" in str(exc_info.value)
    finally:
        _reset_worker_main()
