---
id: task-01
milestone: m5-queue-routing
depends_on: []
status: planned
spec: PRD.md §3 (ingest returns `202` in <100 ms; ALL LLM work happens in the worker), §4 (ARQ; Redis 7), §6.1 step 3 (enqueue `triage(alert_id)` on ARQ and return), §6.2 (the job: load → run → persist), §10.1 (no public request path ever invokes the LLM), §11 (six containers incl. `worker` + `redis`), §12 M5; CONVENTIONS.md §2 (contract 3 becomes absolute — the M2 `ignore_imports` exception is deleted; contract 5 covers `worker.main`), §5 (`api/main.py` wiring only), §7 (new settings ship with `.env.example` lines), §10 (skip-by-fixture-name; mock only external seams), §11 (`worker` shares the api image with a different `command:`); `.claude/rules/{api,worker,infra,tests}.md`; M5 spine Global Constraints M5-a (enqueue only after the insert is committed) and M5-b (settings→pipeline wiring pins move to the worker entrypoint)
---

# task-01 — Queue split: `core/queue.py` (job name, job id, `enqueue_triage`, Redis client factory), the ingest route enqueues and answers `pending`, `api/main.py` stops building the pipeline (contract-3 exception deleted), `worker/jobs.py::triage_alert_job` + `worker/main.py::WorkerSettings` (startup/shutdown own the engine and the `httpx` client), compose `redis` + `worker` services, CI/test Redis fixtures, `<100 ms` ingest test

## Goal

After this task the only thing `POST /api/v1/alerts` does with a new alert is insert it, commit,
and enqueue one ARQ job named `triage_alert` with job id `triage:<alert_id>` on the queue
`sentinelbrief:triage`; the response is `202 {"id", "status": "pending", "created": true}` and the
route awaits nothing else. A separate `worker` container (`arq worker.main.WorkerSettings`, same
image as `api`) runs `worker/jobs.py::triage_alert_job`, which loads the alert and runs the M4
pipeline (`TriagePipeline.triage_alert`, unchanged semantics in this task: one attempt, a
validation/LLM failure marks the alert `failed`; task-02 adds retries) — so every LLM call now
happens in the worker process and never in the api process (PRD §3, §10.1). `api/main.py` no
longer imports `worker.*`; the two `ignore_imports` lines of import-linter contract 3 are deleted
and the contract is verified with the ritual. The M2 inline-triage tests are retired or moved:
the `LLM_API_KEY`/`CHEAP_MODEL`/unpriced-model fail-fast pins move to `worker/main.py`'s tests
(spine M5-b). Redis arrives in dev compose (`redis:7-alpine`, loopback-published, named volume),
the `worker` service arrives with the `infra/geoip` read-only mount (M4 task-03 M6), CI gains a
`redis:7` service container and `TEST_REDIS_URL`, and the suite gains skip-by-name Redis fixtures.
The worker entrypoint owns the process-lifetime `httpx.AsyncClient` and the engine and closes both
in ARQ's shutdown hook (M4 task-06 I4); `evals/run.py` builds its registry once per run over a
client it closes (M4 N-M5). Redis being down at ingest is a `503 {"error": {"code":
"queue_unavailable"}}` after the row is committed, and a duplicate POST of a still-`pending` alert
re-enqueues (idempotent at the queue: ARQ refuses a second job with the same id) — the shipper's
retry heals an orphaned row instead of stranding it.

## Context (read ONLY these)

- `PRD.md` §3, §4 (ARQ/Redis rows), §6.1, §6.2 first paragraph, §10.1, §11, §12 M5.
- `docs/plans/m5-queue-routing.md` — Global Constraints (M5-a, M5-b, contract-3 removal, the
  latency test, the env-export line with `TEST_REDIS_URL`).
- `CONVENTIONS.md` §2 (contracts 3 and 5, the contract-verification ritual), §4, §5, §7, §10,
  §11; `.claude/rules/{api,worker,infra,tests}.md`.
- Code you change: `api/deps.py`, `api/factory.py`, `api/main.py`, `api/errors.py`,
  `api/routes/alerts.py`, `core/config.py`, `core/errors.py`, `worker/triage.py`
  (`from_settings` only), `worker/tools/wiring.py` (docstring only), `evals/run.py`,
  `infra/docker-compose.yml`, `.github/workflows/ci.yml`, `tests/conftest.py`, `.env.example`,
  `README.md`, `pyproject.toml`.
- Tests you re-open (pinned by earlier tasks; the test-author re-pins): `tests/test_api_main.py`,
  `tests/test_ingest.py`, `tests/test_inline_triage.py` (renamed, see Files),
  `tests/test_tool_loop.py`, `tests/test_tool_wiring_and_retry_trace.py`,
  `tests/test_evals_run.py`, `tests/test_compose_config.py`, `tests/test_env_example_roster.py`.
- ARQ 0.28 facts the design relies on (verified against the installed package at briefing time;
  the implementer re-verifies with `uv run python -c "import arq; print(arq.VERSION)"`):
  `arq.connections.ArqRedis` is a `redis.asyncio.Redis` subclass constructible synchronously with
  `ArqRedis(connection_pool=ConnectionPool.from_url(url, socket_timeout=…,
  socket_connect_timeout=…), default_queue_name=…)` (connects lazily — usable from module-level
  wiring); `ArqRedis.enqueue_job(function, *args, _job_id=…, _queue_name=…)` returns `None` when a
  job with that id is already queued, in progress, or has a kept result (default
  `keep_result` 3600 s); the queue is the sorted set named by `queue_name`, a running job holds
  `arq:in-progress:<job_id>` for `job_timeout + 10` s, the job's kept result is
  `arq:result:<job_id>`; `arq.worker.func(coro, name=…)` names a job function; `Worker(functions,
  queue_name, redis_settings, burst=True, poll_delay, ctx, on_startup, on_shutdown, job_timeout,
  max_jobs, health_check_interval, retry_jobs)` + `await worker.main()` drains the queue in tests;
  the worker puts its pool at `ctx["redis"]` before calling `on_startup(ctx)`; `arq --check
  <WorkerSettings path>` exits 0 while the health key `<queue_name>:health-check` (TTL
  `health_check_interval + 1` s, refreshed every `health_check_interval` s) exists; `arq
  --help` lists the CLI. `arq` and `redis` both ship `py.typed`.

## Files

- Create: `core/queue.py`, `worker/jobs.py`, `worker/main.py`
- Create (test-author): `tests/test_queue.py`, `tests/test_worker_job.py`,
  `tests/test_worker_main.py`, `tests/test_triage_alert.py` (= `git mv
  tests/test_inline_triage.py`, then edited — see Interfaces → test table)
- Modify (test-author, re-pinned): `tests/conftest.py` (Redis fixtures + skip-by-name),
  `tests/test_api_main.py`, `tests/test_ingest.py`, `tests/test_tool_loop.py`,
  `tests/test_tool_wiring_and_retry_trace.py`, `tests/test_evals_run.py`,
  `tests/test_compose_config.py`, `tests/test_env_example_roster.py` (`REDIS_URL` leaves
  `_SCHEDULED`)
- Modify: `pyproject.toml` (deps `arq>=0.26,<1`, `redis>=5,<6`; contract 3 loses its
  `ignore_imports` and comment), `core/config.py`, `core/errors.py`, `api/errors.py`,
  `api/deps.py`, `api/factory.py`, `api/routes/alerts.py`, `api/main.py`, `worker/triage.py`,
  `worker/tools/wiring.py`, `evals/run.py`, `infra/docker-compose.yml`,
  `.github/workflows/ci.yml`, `.env.example`, `README.md`, `api/openapi.json` +
  `web/src/types/generated/*` (the ingest operation's description text changes → regenerate both
  in the same commit, CONVENTIONS §8)
- Modify (docs invalidated by this task — review I1 / plan conflict P1, fix round 1):
  `CONVENTIONS.md` (§2 item 3: "No exceptions" replaces the M2-only exception; §4's registry
  carve-out sentence no longer mentions inline triage; §5's `create_app` signature gains
  `enqueue`/`redis`/`cache`), `.claude/rules/api.md` (no exception to contract 3; the ingest
  route's commit-before-enqueue STAYS — spine M5-a)
- Delete: nothing else (`tests/test_inline_triage.py` is renamed, not deleted)

## Interfaces

- **Consumes:** `insert_alert`, `get_alert`, `set_alert_status` (`core.services.alerts`);
  `TriagePipeline` (`worker.triage`: `from_settings`, `triage_alert`, `tool_names`);
  `build_registry` (`worker.tools.wiring`); `OpenAICompatibleLLMClient.from_settings`;
  `make_engine`, `make_session_factory` (`core.db`); `Settings`; `TOOL_NAMES`; `FakeLLMClient`.
- **Produces (tasks 02–05 rely on — produce exactly):**

  ```python
  # core/config.py — new fields (+ .env.example lines, see below)
  redis_url: SecretStr = SecretStr("")                                     # REDIS_URL — graduates from _SCHEDULED; SECRET (a deployed URL may carry a password)
  redis_socket_timeout_s: Annotated[float, Field(gt=0)] = 2.0              # REDIS_SOCKET_TIMEOUT_S=2 — connect + socket timeout of every api-side Redis call, so /healthz and enqueue fail fast
  triage_job_timeout_s: Annotated[int, Field(ge=1)] = 120                  # TRIAGE_JOB_TIMEOUT_S=120 — ARQ job_timeout; the in-progress lease ARQ holds is this + 10 s (task-04's kill test waits it out)
  worker_max_jobs: Annotated[int, Field(ge=1)] = 4                         # WORKER_MAX_JOBS=4 — concurrent jobs per worker process (bounds concurrent LLM calls)
  worker_health_check_interval_s: Annotated[int, Field(ge=1)] = 15         # WORKER_HEALTH_CHECK_INTERVAL_S=15 — ARQ refreshes `<queue>:health-check` (TTL interval+1 s) this often; compose probes it every 30 s
  def require_nonempty(name: str, value: str) -> None                      # moved out of api/main.py (both entrypoints use it): raises ConfigError(f"{name} must not be empty")

  # core/errors.py — new member (+ api/errors.py STATUS_BY_ERROR row: QueueUnavailableError -> 503; message hidden behind GENERIC_MESSAGE like every ≥500 mapping)
  class QueueUnavailableError(SentinelBriefError): code = "queue_unavailable"

  # core/queue.py — the ONLY module that knows ARQ's job/queue names; api and worker both import it (api never imports worker)
  TRIAGE_JOB_NAME = "triage_alert"
  TRIAGE_QUEUE_NAME = "sentinelbrief:triage"          # the ARQ queue sorted set; ARQ's health key is therefore "sentinelbrief:triage:health-check"
  def triage_job_id(alert_id: uuid.UUID) -> str: ...  # f"triage:{alert_id}" — one job per alert; M8's retriage mints its own id (see task-04)
  def make_redis(redis_url: str, *, socket_timeout_s: float) -> ArqRedis: ...
      # ArqRedis(connection_pool=redis.asyncio.ConnectionPool.from_url(redis_url, socket_timeout=socket_timeout_s, socket_connect_timeout=socket_timeout_s),
      #          default_queue_name=TRIAGE_QUEUE_NAME). Sync construction, lazy connect (module-level wiring has no event loop). The caller owns aclose().
  def redis_settings(redis_url: str) -> RedisSettings: ...   # arq.connections.RedisSettings.from_dsn(redis_url) — the worker's pool settings
  async def enqueue_triage(redis: ArqRedis, alert_id: uuid.UUID) -> bool: ...
      # job = await redis.enqueue_job(TRIAGE_JOB_NAME, str(alert_id), _job_id=triage_job_id(alert_id), _queue_name=TRIAGE_QUEUE_NAME)
      # returns job is not None (False = a job with this id already exists: queued, running, or its result is still kept)
      # redis.exceptions.RedisError | OSError -> raise QueueUnavailableError("triage queue unavailable") from exc
      #   — the message never contains the URL, a host, or a password (the exception text of the cause is not echoed)

  # api/deps.py — TriageFn/get_triage are DELETED; the route layer only ever sees an enqueue callable
  EnqueueFn = Callable[[uuid.UUID], Awaitable[None]]
  def get_enqueue(request: Request) -> EnqueueFn: ...        # RuntimeError("no enqueue wired") when unwired

  # api/factory.py
  def create_app(*, session_factory=None, settings=None, enqueue: EnqueueFn | None = None,
                 redis: Redis | None = None, cache: TTLCache | None = None) -> FastAPI
      # app.state.enqueue = enqueue; app.state.redis = redis (None when unwired: task-05's /healthz answers redis="unconfigured");
      # app.state.triage no longer exists. A lifespan handler awaits `app.state.redis.aclose()` on shutdown when redis is not None
      # (`from redis.asyncio import Redis` for the annotation; DB-less/Redis-less construction still succeeds).

  # api/routes/alerts.py — POST /api/v1/alerts (operation_id unchanged: "ingest_alert")
  async def ingest_alert(payload: SessionAlert, session: SessionDep, enqueue: EnqueueFn = Depends(get_enqueue)) -> JSONResponse
      # result = await insert_alert(session, payload)
      # if result.created or result.status == "pending":
      #     await session.commit()          # spine M5-a: the row must be durable BEFORE the job can be picked up; SessionDep's later commit is a no-op.
      #                                    # (The M2 comment is reworded to say this, not deleted.)
      #     await enqueue(result.alert_id)  # QueueUnavailableError -> 503 envelope; the committed row stays `pending` and the shipper's retry re-enqueues it
      # body = IngestResponse(id=result.alert_id, status=result.status, created=result.created)   # a created alert answers "pending"
      # 202 when created, 200 otherwise. Duplicates of a triaged/failed alert never enqueue (PRD §6.1: duplicates never re-trigger triage).

  # api/main.py — wiring only; imports nothing from worker.*
  settings = Settings(); require_nonempty("DATABASE_URL" | "INGEST_HMAC_SECRET" | "REDIS_URL", …)      # LLM_API_KEY/CHEAP_MODEL are the worker's concern now
  engine / session_factory as today
  redis_client: ArqRedis = make_redis(settings.redis_url.get_secret_value(), socket_timeout_s=settings.redis_socket_timeout_s)
  async def enqueue(alert_id: uuid.UUID) -> None: await enqueue_triage(redis_client, alert_id)
  app = create_app(session_factory=session_factory, settings=settings, enqueue=enqueue, redis=redis_client)   # cache= stays default until task-05

  # worker/jobs.py — the ARQ job function (task-02 grows the except-branch into the retry policy; task-04 adds the FOR UPDATE skip)
  JobResult = Literal["triaged", "failed", "skipped", "missing"]     # "skipped" is minted by task-04; declared now so the alias never changes shape
  async def triage_alert_job(ctx: Mapping[str, Any], alert_id: str) -> JobResult: ...
      # alert_uuid = uuid.UUID(alert_id)         -> ValueError propagates (a malformed id can only come from our own enqueue: a bug, never retried)
      # pipeline: TriagePipeline = ctx["pipeline"]; factory = ctx["session_factory"]
      # async with factory() as session:
      #     try:    return await pipeline.triage_alert(session, alert_uuid)     # "triaged" | "failed" (M2 semantics in this task)
      #     except NotFoundError: logger.warning("triage job: alert not found alert_id=%s job_id=%s", alert_id, ctx.get("job_id")); return "missing"
      # Logs ids only — never the payload (rules/worker.md).

  # worker/main.py — the ARQ entrypoint (nothing imports it: contract 5)
  logging.basicConfig(level=INFO) at import (mirrors api/main.py)
  settings = Settings(); require_nonempty for DATABASE_URL, REDIS_URL, LLM_API_KEY, CHEAP_MODEL (in that order; ConfigError names the first empty one)
  async def startup(ctx: dict[str, Any]) -> None: ...
      # s: Settings = ctx["settings"]
      # engine = make_engine(s.database_url.get_secret_value()); ctx["engine"] = engine; ctx["session_factory"] = make_session_factory(engine)
      # ctx["http"] = httpx.AsyncClient(timeout=s.abuseipdb_timeout_s)
      # llm = OpenAICompatibleLLMClient.from_settings(s)                   # ConfigError for an unpriced/empty model — raised here, before any job runs
      # ctx["pipeline"] = TriagePipeline.from_settings(s, llm=llm, http=ctx["http"])     # cache=None -> in-process AbuseIPDB cache until task-05
      # logger.info("worker ready model=%s prompt=%s tools=%s", s.cheap_model, s.triage_prompt_version, ctx["pipeline"].tool_names)
  async def shutdown(ctx: dict[str, Any]) -> None: ...
      # http = ctx.pop("http", None); if http is not None: await http.aclose()
      # engine = ctx.pop("engine", None); if engine is not None: await engine.dispose()
      # (pop, so a second shutdown is a no-op)
  class WorkerSettings:
      functions = [func(triage_alert_job, name=TRIAGE_JOB_NAME)]
      queue_name = TRIAGE_QUEUE_NAME
      redis_settings = redis_settings(settings.redis_url.get_secret_value())
      on_startup = startup
      on_shutdown = shutdown
      ctx = {"settings": settings}
      job_timeout = settings.triage_job_timeout_s
      max_jobs = settings.worker_max_jobs
      health_check_interval = settings.worker_health_check_interval_s
      retry_jobs = True
      # max_tries is set by task-02 (from TRIAGE_JOB_MAX_TRIES); ARQ's default (5) stands until then

  # worker/triage.py — from_settings gains the http seam (I4 ownership: the CALLER owns the client)
  @classmethod
  def from_settings(cls, settings, *, llm, recorder=None, cache=None, http: httpx.AsyncClient | None = None) -> TriagePipeline
      # ... tools=build_registry(settings, recorder=recorder or LiveToolRecorder(), cache=cache, http=http) ...
  # worker/tools/wiring.py docstring: the ownership paragraph now names worker/main.py::shutdown as the aclose() owner (no code change)

  # evals/run.py — one registry per run (N-M5) and a closed client
  def main(argv=None, *, llm: LLMClient | None = None, http: httpx.AsyncClient | None = None) -> int
      # http = http or httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s), built AFTER Settings() validates;
      # registry = build_registry(settings, recorder=ReplayToolRecorder(args.tool_fixtures), http=http) ONCE, above `for prompt_version in args.prompt`;
      # every TriagePipeline in the loop gets tools=registry; `await http.aclose()` runs in a `finally` around the whole prompt loop
      # (also on the all_cases_failed / output_error / ConfigError exits) — via one `asyncio.run(_run_all(...))` wrapper or an explicit
      # `asyncio.run(http.aclose())` in the finally; either is fine, the test pins `http.is_closed` after main returns on both the 0 and 1 paths.

  # infra/docker-compose.yml — two new services (api gains `redis: condition: service_healthy` in depends_on)
  redis:  image redis:7-alpine; command ["redis-server", "--save", "60", "1", "--loglevel", "warning"]   # RDB snapshot ≤ 60 s after a change: queued jobs survive a restart
          ports ["127.0.0.1:6379:6379"]; volumes [sentinelbrief_redis:/data]; healthcheck ["CMD", "redis-cli", "ping"] interval 5s timeout 3s retries 10; logging *default-logging
  worker: build {context: .., dockerfile: infra/Dockerfile.api} (identical to api — the layer cache makes the second build free); image sentinelbrief-api
          command ["arq", "worker.main.WorkerSettings"]; env_file ../.env; NO ports
          volumes ["../infra/geoip:/app/infra/geoip:ro"]        # M4 task-03 M6: the worker is where get_ip_geo_asn runs now
          depends_on postgres + redis: condition service_healthy
          healthcheck ["CMD", "arq", "--check", "worker.main.WorkerSettings"] interval 30s timeout 10s start_period 20s retries 3
          logging *default-logging
  volumes: sentinelbrief_pg, sentinelbrief_redis
  # The header comment ("Three services…; worker and redis arrive at M5") is rewritten to the five-service list.

  # .github/workflows/ci.yml — job `python` gains a `redis` service (image redis:7, port 6379:6379, options --health-cmd "redis-cli ping" …)
  # and env TEST_REDIS_URL: redis://localhost:6379/0. The existing "no skips allowed" grep now also proves the Redis suite ran.

  # tests/conftest.py — Redis fixtures, skipped BY NAME when TEST_REDIS_URL is unset (recorded, never a pass)
  _REDIS_FIXTURE_NAMES = {"redis_url", "arq_redis"}
  @pytest.fixture def redis_url() -> str                     # os.environ["TEST_REDIS_URL"] or pytest.skip("TEST_REDIS_URL unset")
  @pytest.fixture async def arq_redis(redis_url) -> AsyncIterator[ArqRedis]
      # client = make_redis(redis_url, socket_timeout_s=2.0); await client.flushdb() before yield AND after; await client.aclose()
      # flushdb is why TEST_REDIS_URL must point at a DEDICATED instance (127.0.0.1:6380), never the dev compose Redis on 6379.
  # pytest_collection_modifyitems: DB names skip without TEST_DATABASE_URL (unchanged); Redis names skip without TEST_REDIS_URL.

  # .env.example — new section between "Persistence…" and "Ingest & admin auth":
  # # ---------------------------------------------------------------------------------------------
  # # Queue & worker (from M5)
  # # ---------------------------------------------------------------------------------------------
  # # Connect + socket timeout for the api's Redis calls (enqueue, /healthz ping, response cache), in seconds.
  # REDIS_SOCKET_TIMEOUT_S=2
  # # ARQ job timeout for one triage job (all attempts share it); ARQ's in-progress lease is this + 10 s.
  # TRIAGE_JOB_TIMEOUT_S=120
  # # Concurrent triage jobs per worker process (bounds concurrent LLM calls).
  # WORKER_MAX_JOBS=4
  # # How often the worker refreshes its Redis health key (the compose healthcheck probes it every 30 s).
  # WORKER_HEALTH_CHECK_INTERVAL_S=15
  # REDIS_URL's existing comment gains "SECRET (may embed a password)"; the dev/test section gains
  # # TEST_REDIS_URL=redis://127.0.0.1:6380/0   (commented, like TEST_DATABASE_URL, with the "dedicated instance — flushdb" warning)
  ```

## Interfaces → test table

Every DB test uses the throwaway schema; every Redis test uses `arq_redis` (flushed). Ingest tests
drive the real app over `ASGITransport` with a `FakeEnqueue` (records `calls: list[uuid.UUID]`;
optional `error: Exception | None` raised after recording; optional `session_factory` — when set,
it opens a FRESH session on each call and appends the row's status as seen there to `observed`).
`FakeEnqueue` is a fake of the external queue seam, not of our code.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `enqueue_triage` queues | `tests/test_queue.py::test_enqueue_triage_queues_one_job_with_the_alert_job_id` | returns `True`; `await arq_redis.zscore(TRIAGE_QUEUE_NAME, triage_job_id(aid))` is not `None`; `await Job(triage_job_id(aid), arq_redis, _queue_name=TRIAGE_QUEUE_NAME).info()` has `function == "triage_alert"` and `args == (str(aid),)` |
| idempotent per id | `tests/test_queue.py::test_enqueue_triage_is_idempotent_per_alert_id` | second call returns `False`; `zcard == 1` |
| dead Redis | `tests/test_queue.py::test_enqueue_triage_dead_redis_is_queue_unavailable_and_never_leaks_the_url` | `make_redis("redis://:queue-pw-1@127.0.0.1:1/0", socket_timeout_s=0.5)` (port 1 refuses immediately) → `QueueUnavailableError`; `"queue-pw-1" not in str(exc)` and `"127.0.0.1" not in str(exc)`; `"queue-pw-1" not in caplog.text` |
| client factory | `tests/test_queue.py::test_make_redis_applies_the_socket_timeouts_and_queue_name` | `make_redis(url, socket_timeout_s=2.5)`: `connection_pool.connection_kwargs["socket_timeout"] == 2.5 == connection_kwargs["socket_connect_timeout"]`; `default_queue_name == TRIAGE_QUEUE_NAME` (distinguishable value ≠ the Setting's default) |
| job id | `tests/test_queue.py::test_triage_job_id_shape` | `triage_job_id(u) == f"triage:{u}"` |
| 503 envelope | `tests/test_queue.py::test_queue_unavailable_maps_to_503_envelope` | probe route on a DB-less `create_app()` raising `QueueUnavailableError("x")` → 503, `error.code == "queue_unavailable"`, `error.message == GENERIC_MESSAGE` (never `"x"`) |
| settings | `tests/test_queue.py::test_queue_settings_defaults_bounds_and_secret_repr` | literals with a comment (R17: a `Settings()` comparison is tautological): `2.0 / 120 / 4 / 15`; `redis_socket_timeout_s=0`, `worker_max_jobs=0`, `triage_job_timeout_s=0` → `ValidationError`; `repr(Settings(redis_url=SecretStr("redis://:repr-pw-2@h/0")))` contains no `repr-pw-2`; roster green after `REDIS_URL` graduates |
| burst worker triages | `tests/test_worker_job.py::test_enqueued_job_triages_the_alert_through_a_burst_worker` | `seed_alert(session, "alert4")` (no verdict → `pending`), commit; `enqueue_triage`; `Worker(functions=[func(triage_alert_job, name=TRIAGE_JOB_NAME)], queue_name=TRIAGE_QUEUE_NAME, redis_settings=redis_settings(redis_url), burst=True, poll_delay=0.01, ctx={"pipeline": TriagePipeline(llm=FakeLLMClient([VALID4]), model="fake-model", prompt_version="triage-v1"), "session_factory": db_session_factory})`; `await worker.main(); await worker.close()` → status `triaged`, 1 verdict, `worker.jobs_complete == 1`, `await Job(...).result() == "triaged"`, `zcard == 0`. **Computed:** one `complete_structured` call (no tools) |
| missing alert | `tests/test_worker_job.py::test_job_for_a_missing_alert_returns_missing_without_retry` | enqueue `uuid4()` → result `"missing"`, `jobs_complete == 1`, `jobs_retried == 0`, one WARNING in caplog containing the id |
| failure → failed (this task) | `tests/test_worker_job.py::test_job_failure_marks_the_alert_failed` | `FakeLLMClient([LLMCallError("boom")])` → result `"failed"`, status `failed`, 0 verdicts, `jobs_failed == 0` (the job itself completed). Task-02 re-pins this file with the retry policy — the docstring says so |
| malformed id | `tests/test_worker_job.py::test_job_rejects_a_malformed_alert_id` | `await triage_alert_job({"pipeline": …, "session_factory": …}, "not-a-uuid")` → `ValueError` (direct call, no worker) |
| worker fail-fast | `tests/test_worker_main.py::test_missing_{database_url,redis_url,llm_api_key,cheap_model}_raises_config_error` (four tests) | `importlib.import_module("worker.main")` with the named var empty (others set) → `ConfigError` naming it; `sys.modules.pop("worker.main")` before and after each (the `_reset` pattern from `tests/test_api_main.py`) |
| WorkerSettings reads Settings | `tests/test_worker_main.py::test_worker_settings_are_read_from_settings` | env `REDIS_URL=redis://:pw@127.0.0.1:6390/3`, `TRIAGE_JOB_TIMEOUT_S=77`, `WORKER_MAX_JOBS=3`, `WORKER_HEALTH_CHECK_INTERVAL_S=11` → `redis_settings.host == "127.0.0.1"`, `.port == 6390`, `.database == 3`, `.password == "pw"`; `job_timeout == 77`; `max_jobs == 3`; `health_check_interval == 11`; `queue_name == TRIAGE_QUEUE_NAME`; `[f.name for f in functions] == ["triage_alert"]`; `on_startup is startup`; `on_shutdown is shutdown`; `retry_jobs is True`; `ctx["settings"].triage_job_timeout_s == 77` (distinguishable values per seam, rule 7) |
| startup/shutdown own the seams | `tests/test_worker_main.py::test_startup_builds_the_seams_and_shutdown_closes_them` | `ctx = {"settings": Settings(llm_api_key=SecretStr("sk-test"), cheap_model="fake-model", model_prices_json={"fake-model": ModelPrice(0, 0)}, database_url=SecretStr("postgresql://u:p@127.0.0.1:1/x"), abuseipdb_timeout_s=7.5)}` (no DB/Redis connection is made); `await startup(ctx)` → `ctx["pipeline"].tool_names == TOOL_NAMES`, `ctx["http"].timeout.read == 7.5`, `isinstance(ctx["session_factory"], async_sessionmaker)`; `await shutdown(ctx)` → `http.is_closed is True`, `"http" not in ctx`, `"engine" not in ctx`; `await shutdown(ctx)` again → no error |
| unpriced model at startup | `tests/test_worker_main.py::test_startup_unpriced_cheap_model_is_a_config_error` | same ctx with `model_prices_json={}` → `ConfigError` mentioning `fake-model` (the pin that leaves `tests/test_api_main.py`) |
| api fail-fast | `tests/test_api_main.py::test_missing_{database_url,ingest_hmac_secret,redis_url}_raises_config_error` | as today's pattern; the `LLM_API_KEY`/`CHEAP_MODEL`/unpriced tests are DELETED here (moved above) |
| api wiring | `tests/test_api_main.py::test_required_values_present_builds_app_with_enqueue_and_redis` | env has NO `LLM_API_KEY`/`CHEAP_MODEL`; import succeeds; `callable(module.app.state.enqueue)`; `isinstance(module.app.state.redis, ArqRedis)`; `not hasattr(module.app.state, "triage")` |
| api never imports worker | `tests/test_api_main.py::test_api_main_never_imports_worker_or_core_llm` | subprocess (`cwd=repo root`, env with the three required vars): `import api.main`, then assert no `sys.modules` name equals/starts with `worker`/`core.llm` — the M2 `test_api_routes_never_import_worker` generalized to the entrypoint (moved from `tests/test_inline_triage.py`) |
| ingest → pending + enqueue | `tests/test_ingest.py::test_signed_post_202_pending_and_enqueues_once` | 202; `status == "pending"`, `created is True`; `fake.calls == [alert_id]`; the row's status is `pending` |
| pending duplicate re-enqueues | `tests/test_ingest.py::test_duplicate_of_a_pending_alert_re_enqueues` | two POSTs of the same body → 200, `status == "pending"`, `created is False`, `fake.calls == [id, id]` |
| triaged duplicate does not | `tests/test_ingest.py::test_duplicate_of_a_triaged_alert_does_not_enqueue` | POST; `set_alert_status(…, "triaged")` + commit in a fresh session; POST again → 200, `status == "triaged"`, `len(fake.calls) == 1` |
| M5-a commit-before-enqueue | `tests/test_ingest.py::test_enqueue_sees_the_committed_row` | `FakeEnqueue(session_factory=db_session_factory)` → `fake.observed == ["pending"]` (a fresh session saw the row: it was committed before `enqueue` ran); mutation: drop the route's `commit()` → `observed == [None]` |
| Redis down | `tests/test_ingest.py::test_queue_unavailable_is_503_and_the_row_persists` | `FakeEnqueue(error=QueueUnavailableError("down"))` → 503, `error.code == "queue_unavailable"`; `count_rows_fresh(AlertRow) == 1`, status `pending` |
| <100 ms | `tests/test_ingest.py::test_ingest_route_latency_under_budget_with_fake_queue` | one warm-up POST, then 5 POSTs of distinct `session_id`s timed with `perf_counter`; `statistics.median(ms) < 100`. Docstring states what it pins (rule 14): no LLM/tool work on the request path — the insert, the commit and an immediately-returning enqueue; the live budget under real Redis is the acceptance burst. If it flakes: a bigger sample, never a bigger budget |
| kept from M2 | `tests/test_ingest.py::{test_unsigned_post_401, test_bad_signature_401, test_non_ascii_signature_header_is_401_not_500, test_invalid_payload_422, test_signature_checked_before_body_validation, test_signed_router_holds_only_the_ingest_post}` | unchanged assertions, `FakeTriage` → `FakeEnqueue`; `test_signed_post_status_reflects_triage_outcome` and `test_duplicate_post_does_not_invoke_triage` are DELETED (their behaviour no longer exists) |
| `triage_alert` direct pins | `tests/test_triage_alert.py` (renamed from `test_inline_triage.py`): keep `test_triage_alert_direct_returns_triaged_and_commits`, `test_triage_alert_direct_failure_commits_failed_status`, `test_triage_alert_failure_rolls_back_dirty_session`, `test_triage_alert_unknown_alert_raises_not_found` | the five route-level inline tests and `_build_app` are DELETED; `test_api_routes_never_import_worker` moves to `tests/test_api_main.py` (above); module docstring rewritten |
| loop file first touch | `tests/test_tool_loop.py` | DELETE `test_api_main_pipeline_has_the_five_tools` + `_reset_api_main` (no pipeline in `api.main`); RENAME `test_tools_without_a_cap_is_a_value_error_and_from_settings_honours_the_cap_setting` → `test_tools_without_a_cap_is_a_value_error` (M4 fix-wave re-review N1, verbatim) |
| http seam through `from_settings` | `tests/test_tool_wiring_and_retry_trace.py::test_from_settings_passes_the_http_client_to_the_registry` | `client = httpx.AsyncClient(timeout=3.25)`; `TriagePipeline.from_settings(settings, llm=FakeLLMClient([]), http=client)` → the registry's `lookup_ip_reputation` tool has `_http is client` (the private-attribute pattern accepted in M4); the module docstring lists everything the file now hosts (M4 re-review N2) |
| evals registry once + closed | `tests/test_evals_run.py::test_main_closes_the_injected_http_client_on_success_and_failure` | `main([--golden v1 --prompt triage-v1 --prompt triage-v2 --concurrency 1 …], llm=fake, http=client)` → `0` and `client.is_closed`; a second run with a fake that fails every case → `1` (`all_cases_failed`) and its client `is_closed`. (The registry hoist itself is a review item with `file:line`: `build_registry(` appears once in `evals/run.py`, above the prompt loop.) |
| compose redis | `tests/test_compose_config.py::test_compose_redis_service_shape` | image `redis:7-alpine`; one port `host_ip 127.0.0.1`, target `6379`; healthcheck test joined contains `redis-cli ping`; volume source `sentinelbrief_redis` → target `/data`; `sentinelbrief_redis` in top-level volumes; logging json-file `max-size 10m` |
| compose worker | `tests/test_compose_config.py::test_compose_worker_service_shape` | rendered against `_ENV_FILE_LEAK_CANARY`: `command == ["arq", "worker.main.WorkerSettings"]`; `"ports" not in worker` (or empty); build context resolves to the repo-root stand-in and dockerfile ends with `infra/Dockerfile.api`; `image == "sentinelbrief-api"`; `environment["SENTINELBRIEF_TEST_CANARY"] == "canary"` (the env_file IS applied — the worker needs the secrets, unlike `web`); geoip bind mount `read_only` at `/app/infra/geoip`; `depends_on["postgres"]["condition"] == depends_on["redis"]["condition"] == "service_healthy"`; healthcheck test joined contains `arq --check worker.main.WorkerSettings`; logging as api |
| api waits for redis | `tests/test_compose_config.py::test_compose_api_depends_on_healthy_redis` | `api.depends_on.redis.condition == "service_healthy"` |
| existing compose pins | `test_compose_config_validates` (+ `redis`, `worker` present), `test_compose_publishes_loopback_only` (+ `redis == {"6379"}`, `"worker" not in published_targets`) | assertions extended, nothing else changes |
| roster | `tests/test_env_example_roster.py` | `REDIS_URL` removed from `_SCHEDULED`; both roster tests green after the `.env.example` lines land |
| lifespan closes redis (review I2 / P2, fix-1) | `tests/test_app_lifespan.py::test_lifespan_closes_the_wired_redis_client_on_shutdown`, `::test_lifespan_is_a_no_op_when_no_redis_is_wired` | `httpx.ASGITransport` never runs the ASGI lifespan, so this is driven through `app.router.lifespan_context(app)`: a recording fake with `aclose()` sees exactly `["aclose"]`; a DB-less/Redis-less app starts and stops cleanly. Mutation: `aclose` → `close` in `api/factory.py` fails the first test |
| api.main enqueue seam (review M1, fix-1) | `tests/test_api_main_enqueue.py::test_api_main_enqueue_closure_reaches_enqueue_triage` | env `REDIS_URL=redis://127.0.0.1:1/0` (refuses immediately), `REDIS_SOCKET_TIMEOUT_S=0.5`; `await module.app.state.enqueue(uuid4())` raises `QueueUnavailableError` — the closure really reaches `enqueue_triage` on the wired client; mutation: a no-op closure fails it |
| registry once (review M3, fix-1) | `tests/test_evals_registry_once.py::test_evals_run_builds_the_registry_once_above_the_prompt_loop` | source-level pin: `evals/run.py` contains `build_registry(` exactly once, before `for prompt_version in args.prompt:` (N-M5 has no black-box seam) |
| redis snapshot policy (review M2, fix-1) | `tests/test_compose_config.py::test_compose_redis_service_shape` | + `redis["command"] == ["redis-server", "--save", "60", "1", "--loglevel", "warning"]` — queued jobs survive a restart; the docstring's RDB clause is now pinned |
| contract 3 absolute | (implementer ritual, pasted in the report) | inject `from worker.triage import TriagePipeline` into `api/deps.py` → `uv run lint-imports` exit 1 naming contract 3; revert → exit 0; `lint-imports` output shows `5 contracts KEPT` with **0 ignored imports** |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins every file it touched (new and re-opened);
the **implementer** does Steps 3–8 and never edits a pinned file. Because `tests/conftest.py`
gains the Redis fixtures in Step 1, the test-author starts the dedicated test Redis first:

```bash
docker run -d --name sentinelbrief-test-redis -p 127.0.0.1:6380:6379 redis:7-alpine   # once per machine; `docker start sentinelbrief-test-redis` afterwards
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
```

- [ ] **Step 1 (RED — test-author): `git mv tests/test_inline_triage.py tests/test_triage_alert.py`;
  write/re-open every file in the table.** `tests/conftest.py` gains the two Redis fixtures and the
  Redis half of the collection hook; `tests/test_env_example_roster.py` drops `REDIS_URL` from
  `_SCHEDULED`. New test modules import `core.queue`, `worker.jobs`, `worker.main` at module top
  (so RED is a clean `ModuleNotFoundError`), except `tests/test_worker_main.py`, which imports
  `worker.main` inside each test after setting env (the fail-fast pattern). `FakeEnqueue` lives in
  `tests/test_ingest.py` (a fake of the queue seam).
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q tests/test_queue.py
  tests/test_worker_job.py tests/test_worker_main.py tests/test_ingest.py tests/test_api_main.py
  tests/test_compose_config.py tests/test_env_example_roster.py tests/test_evals_run.py
  tests/test_tool_wiring_and_retry_trace.py` → Expected: `ModuleNotFoundError: No module named
  'core.queue'` / `'worker.jobs'` / `'worker.main'`; `ImportError: cannot import name
  'EnqueueFn' from 'api.deps'`; compose tests fail on the missing `redis`/`worker` services; the
  roster test fails until `Settings.redis_url` exists; the evals test fails with `TypeError:
  main() got an unexpected keyword argument 'http'`. Also run `uv run pytest -q
  tests/test_tool_loop.py tests/test_triage_alert.py` → green (rename/deletions only). Record
  `-rs` output with BOTH env vars exported (0 skipped). Pin, commit `test(api,worker,infra): queue
  split RED — enqueue seam, worker entrypoint, Redis fixtures, compose worker/redis (m5 task-01)`.
- [ ] **Step 3 (GREEN — implementer): dependencies + settings + errors.** `uv add "arq>=0.26,<1"
  "redis>=5,<6"` (commit `uv.lock`); `core/config.py` fields + `require_nonempty`;
  `core/errors.py::QueueUnavailableError`; `api/errors.py` row; `.env.example` section. `uv run
  mypy` clean; roster test green.
- [ ] **Step 4 (GREEN — implementer): `core/queue.py` + api side.** `api/deps.py` (`EnqueueFn`,
  `get_enqueue`; delete `TriageFn`/`get_triage`), `api/factory.py` (`enqueue`, `redis`, lifespan
  aclose), `api/routes/alerts.py`, `api/main.py`. Then `pyproject.toml`: delete the contract-3
  `ignore_imports` line and its comment; **run the contract ritual** and paste both outcomes into
  the report. Regenerate `api/openapi.json` (`uv run python scripts/export_openapi.py`) and `pnpm
  -C web codegen`; commit both with this change. `uv run mypy` clean.
- [ ] **Step 5 (GREEN — implementer): worker side.** `worker/jobs.py`, `worker/main.py`,
  `worker/triage.py::from_settings(http=)`, `worker/tools/wiring.py` docstring, `evals/run.py`
  (hoist + `http=` + `aclose()` in `finally`). `uv run mypy` clean.
- [ ] **Step 6 (GREEN — implementer): infra.** `infra/docker-compose.yml` (`redis`, `worker`,
  api's `depends_on`, header comment, `sentinelbrief_redis` volume), `.github/workflows/ci.yml`
  (redis service + `TEST_REDIS_URL`). `docker compose -f infra/docker-compose.yml config >
  /dev/null` (output NEVER pasted — rules/infra.md).
- [ ] **Step 7 (implementer): prove it live** against the dev stack: `docker compose -f
  infra/docker-compose.yml up -d --build` (five services healthy: `docker compose … ps` pasted),
  migrate, `uv run python scripts/post_alert.py fixtures/alerts/alert2.json` with a fresh
  `session_id` (edit a copy in the scratchpad) → `202 {"status": "pending"}`; `docker compose …
  logs worker | tail` shows the job line; `curl -s localhost:8000/api/v1/alerts/<id>` → `status ==
  "triaged"` within a few seconds; paste the three outputs (ids only, never the payload) into the
  report. `docker compose … exec api python -c "import sys; import api.main; print('worker' in
  sys.modules)"` → `False` (the api container never loads the worker package).
- [ ] **Step 8 (implementer): README** — Quickstart step 3: the five services on `up`, the
  `post_alert` line answers `202 {"status": "pending"}` and the verdict appears via the detail
  endpoint / `docker compose … logs worker`; Gates: the `sentinelbrief-test-redis` `docker run`
  line and the two-variable export line. Then all tests in the table + the existing suite green;
  full gates (cold) with BOTH env vars exported → commit `feat(api,worker,infra): ingest
  enqueues on ARQ, worker container runs triage, contract-3 exception removed (m5 task-01)` with
  the two trailers; path-scoped `git add` (never `.env`).

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_queue.py tests/test_worker_job.py tests/test_worker_main.py tests/test_ingest.py tests/test_api_main.py tests/test_triage_alert.py tests/test_compose_config.py tests/test_env_example_roster.py tests/test_evals_run.py tests/test_tool_wiring_and_retry_trace.py tests/test_tool_loop.py   # all pass, 0 skipped
uv run lint-imports                                           # 5 contracts kept, no "ignored imports" line under contract 3
grep -rnE "^\s*(from|import)\s+worker" api/ --include=*.py ; echo "exit=$?"   # exit=1 — no worker import statement anywhere under api/ (docstring prose may mention the word; review P3)
grep -c "build_registry(" evals/run.py                        # 1
uv run python scripts/export_openapi.py --out /tmp/openapi.json && cmp /tmp/openapi.json api/openapi.json   # no drift
pnpm -C web codegen && git diff --exit-code -- web/src/types/generated                                       # no drift
docker compose -f infra/docker-compose.yml config > /dev/null && echo ok                                     # ok
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90   # clean, 0 skipped
```

## Acceptance

- `POST /api/v1/alerts` inserts, commits, enqueues `triage_alert` with job id `triage:<alert_id>`
  on `sentinelbrief:triage`, and answers `202 pending` — never awaiting an LLM, a tool, or a
  verdict; a duplicate of a `pending` alert re-enqueues (idempotent at the queue), a duplicate of
  a triaged/failed alert does not; Redis down is a `503 queue_unavailable` with the row committed.
- `api/` imports nothing from `worker`; contract 3 has no exceptions and failed the ritual when
  violated; the `LLM_API_KEY`/`CHEAP_MODEL`/unpriced pins live in `tests/test_worker_main.py`.
- `arq worker.main.WorkerSettings` runs `triage_alert_job` from a separate compose service that
  shares the api image, owns and closes its `httpx` client and engine, refreshes a health key
  ARQ's `--check` verifies, and mounts `infra/geoip` read-only; `redis:7-alpine` is loopback-only
  with a named volume; CI runs the Redis suite with zero skips.
- Every new setting has its `.env.example` line; the ingest latency and burst-worker tests pass
  with both env vars exported.
