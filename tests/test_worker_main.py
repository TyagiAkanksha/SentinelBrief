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

m5 task-05 adds `test_startup_installs_a_redis_backed_reputation_cache` and threads a real
`arq_redis` into `ctx["redis"]` for every test that calls `startup(ctx)` directly: in production,
ARQ has already put its own connection pool at `ctx["redis"]` by the time `on_startup` runs (the
task-01 `Context`), so a unit test driving `startup` without a running ARQ worker must supply it
itself — `startup` now builds the worker's `RedisTTLCache` from exactly that key.

m5 fix wave (review N-I1 shape (a)) adds `TRIAGE_ATTEMPT_TIMEOUT_S` to
`test_worker_settings_are_read_from_settings`'s own env/assertion table, and a new
`test_attempt_timeout_at_or_above_job_timeout_raises_config_error` pins the boot-time fail-fast
that keeps the inner attempt deadline strictly below ARQ's own `job_timeout`.
"""

from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from typing import Any

import pytest
from arq.connections import ArqRedis
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.cache import RedisTTLCache
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
    monkeypatch.delenv("TRIAGE_ATTEMPT_TIMEOUT_S", raising=False)
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
    # m5 fix wave (review N-I1 "Pins (test-author)" (1)): below TRIAGE_JOB_TIMEOUT_S, or the boot
    # check below would fire at the default 100 >= 77 before this test ever gets to import.
    monkeypatch.setenv("TRIAGE_ATTEMPT_TIMEOUT_S", "66")
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
        assert ws.ctx["settings"].triage_attempt_timeout_s == 66.0
    finally:
        _reset_worker_main()


def test_attempt_timeout_at_or_above_job_timeout_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """m5 fix wave (review N-I1 shape (a)): `worker/main.py`'s boot check (after the four
    `require_nonempty` lines) fails fast when `TRIAGE_ATTEMPT_TIMEOUT_S` is not strictly below
    `TRIAGE_JOB_TIMEOUT_S` — otherwise ARQ's own `job_timeout` could cancel the whole job from
    OUTSIDE before the inner `asyncio.wait_for` ever fires, recording the job failed with NO
    retry and NO terminal write (the alert would rest `pending` forever)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)
    monkeypatch.delenv("TRIAGE_JOB_TIMEOUT_S", raising=False)
    monkeypatch.delenv("TRIAGE_ATTEMPT_TIMEOUT_S", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON", '{"fake-model":{"input_per_mtok":"0","output_per_mtok":"0"}}'
    )
    monkeypatch.setenv("TRIAGE_JOB_TIMEOUT_S", "60")
    monkeypatch.setenv("TRIAGE_ATTEMPT_TIMEOUT_S", "60")  # AT the job timeout, not just above it
    _reset_worker_main()

    try:
        with pytest.raises(ConfigError) as exc_info:
            importlib.import_module("worker.main")
        assert "TRIAGE_ATTEMPT_TIMEOUT_S" in str(exc_info.value)
        assert "TRIAGE_JOB_TIMEOUT_S" in str(exc_info.value)
    finally:
        _reset_worker_main()


async def test_startup_builds_the_seams_and_shutdown_closes_them(
    monkeypatch: pytest.MonkeyPatch,
    arq_redis: ArqRedis,
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
        ctx: dict[str, Any] = {"settings": settings, "redis": arq_redis}

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


async def test_startup_installs_a_redis_backed_reputation_cache(
    monkeypatch: pytest.MonkeyPatch,
    arq_redis: ArqRedis,
) -> None:
    """m5 task-05 (Interfaces → test table, "worker wiring"): `startup` builds the reputation
    cache from `ctx["redis"]` — the connection pool ARQ has already installed there by the time
    `on_startup` runs — so the free tier's quota (and the `429` back-off flag) survive a worker
    restart, shared across every worker process."""
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
            model_prices_json={
                "fake-model": ModelPrice(input_per_mtok=Decimal("0"), output_per_mtok=Decimal("0"))
            },
            database_url=SecretStr("postgresql://u:p@127.0.0.1:1/x"),
        )
        ctx: dict[str, Any] = {"settings": settings, "redis": arq_redis}

        try:
            await module.startup(ctx)

            # Private-attribute access is the accepted pattern for a wiring pin (task-06 fix-1
            # brief, I1): there is no public accessor for a tool's own configured cache.
            tools = ctx["pipeline"]._tools  # type: ignore[attr-defined]
            reputation_tool = tools._by_name["lookup_ip_reputation"]  # type: ignore[attr-defined]
            assert isinstance(reputation_tool._cache, RedisTTLCache)  # type: ignore[attr-defined]
        finally:
            await module.shutdown(ctx)
    finally:
        _reset_worker_main()


async def test_startup_unpriced_cheap_model_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
    arq_redis: ArqRedis,
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
        ctx: dict[str, Any] = {"settings": settings, "redis": arq_redis}

        with pytest.raises(ConfigError) as exc_info:
            await module.startup(ctx)

        assert "fake-model" in str(exc_info.value)
    finally:
        _reset_worker_main()
