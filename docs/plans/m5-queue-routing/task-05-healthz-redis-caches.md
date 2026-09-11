---
id: task-05
milestone: m5-queue-routing
depends_on: [task-01]
status: planned
spec: PRD.md §8 (`GET /healthz`: DB ping + Redis ping from M5; `503` when degraded; `operation_id` + committed OpenAPI baseline regenerated with any DTO change), §6.3 (`lookup_ip_reputation`: AbuseIPDB free tier with a 24 h **Redis** cache; on quota → `{unavailable}`), §4 (Redis 7 for queue/cache/pubsub); CONVENTIONS.md §4 (the `/healthz` carve-out grows a Redis branch), §6 (`alembic/env.py` is production wiring), §8 (baseline + codegen in the same commit), §10 (mock only external seams); `.claude/rules/{api,worker,core,tests}.md`; M4 final review: task-04 forward note (`cache.get/set` must not raise once Redis backs them), N-M4 (429 negative cache), M3 carry-over N-M3 (a cache hit does no DB work — unpinned), the shared CLI helper (`SUGGESTIONS.md` t6-M6), task-06 M8 (`seed_dev` re-globs the fixture order), M2 final review M2 (`alembic/env.py` empty URL)
---

# task-05 — `/healthz` pings Redis; `core.cache.RedisTTLCache` (never raises) behind the api's list/stats cache and the worker's AbuseIPDB cache; AbuseIPDB quota back-off (negative cache, one key); the cache-hit-does-no-DB-work pin; `core/cli.py` shared CLI helper; `alembic/env.py` refuses an empty URL; `seed_dev.load_candidates` carries the tool names

## Goal

`GET /healthz` reports `{"status", "db", "redis"}`: `200 ok` only when both `SELECT 1` and
`PING` answer, otherwise `503 degraded` with the failing side named (`error`) or `unconfigured`
when the seam is unwired — the compose `HEALTHCHECK` therefore turns the api unhealthy when Redis
is gone, which is what PRD §8 asks. `core/cache.py` gains `RedisTTLCache`, a `TTLCache` over a
`redis.asyncio.Redis` with `SET … PX` / `GET` under the key prefix `sentinelbrief:cache:`; a Redis
failure on `get` is a miss and on `set` is a no-op, each logged once per failure at WARNING with
the exception class (never the URL), so neither the read routes nor `lookup_ip_reputation` can
raise because Redis blinked. `api/main.py` installs it for the list/stats cache and
`worker/main.py::startup` for the reputation cache (24 h, shared across worker restarts and
processes — the free tier's quota finally survives a redeploy). AbuseIPDB's `429` sets ONE
negative key (`abuseipdb:quota_exceeded`, no IP — the quota is account-wide) for
`ABUSEIPDB_QUOTA_BACKOFF_S`; while it exists every lookup answers `unavailable("quota_exceeded")`
without an HTTP call, and `test_failures_are_never_cached` keeps its meaning because the success
key is untouched. The M3 invariant "a cache hit does no DB work" gets its pin (engine `checkout`
counter). Three mechanical carry-overs land in the same task because they touch the same CLIs
and wiring: `core/cli.py` (`UsageError`, `Parser`, `fail`) replaces the three verbatim copies in
`worker/triage_one.py`, `evals/run.py`, `scripts/seed_dev.py`; `alembic/env.py` raises
`ConfigError("DATABASE_URL is not set")` instead of handing SQLAlchemy an empty URL; and
`seed_dev.load_candidates` returns `(alert, canned, tool_names)` triples so `main()` stops
re-globbing the fixture order.

## Context (read ONLY these)

- `PRD.md` §4, §6.3 (reputation row), §8 (`/healthz` row; baseline gate).
- `docs/plans/m5-queue-routing.md` — Global Constraints; the ledgered M5 items list.
- `CONVENTIONS.md` §4, §6, §8, §9 (`scripts/` outside mypy but may import `core.cli`), §10;
  `.claude/rules/{api,core,tests}.md`.
- Task-01 outputs: `app.state.redis` (an `ArqRedis`; `None` when unwired), `make_redis`,
  `worker/main.py::startup` (`cache=None` today), `tests/conftest.py` Redis fixtures.
- Code you build on: `core/cache.py` (`TTLCache`, `InMemoryTTLCache`),
  `api/routes/health.py`, `core/schemas/health.py`, `api/routes/alerts_read.py::_cached_json`,
  `worker/tools/ip_reputation.py` (`CACHE_KEY_PREFIX`, the cache hit/miss/set path),
  `worker/tools/wiring.py::build_registry`, `worker/triage_one.py` / `evals/run.py` /
  `scripts/seed_dev.py` (the `_fail`/`_Parser`/`UsageError` triplets), `alembic/env.py::_database_url`,
  `tests/test_cache.py` (the M3 cache pins), `tests/test_health.py`, `tests/test_ip_reputation_tool.py`,
  `tests/test_seed_dev.py::test_load_candidates_returns_fixtures_then_golden`.
- redis-py 5 facts: `await client.set(key, value, px=ms)`, `await client.get(key) -> bytes | None`
  (no `decode_responses`), `await client.pttl(key)`, `await client.ping()`; failures raise
  subclasses of `redis.exceptions.RedisError` (`ConnectionError`, `TimeoutError`) — `OSError` is
  caught alongside for the raw-socket cases. The `socket_timeout` from task-01's `make_redis`
  bounds every call.

## Files

- Create: `core/cli.py`
- Create (test-author): `tests/test_redis_cache.py`, `tests/test_cache_hit_no_db.py`,
  `tests/test_cli_helper.py`, `tests/test_alembic_env_url.py`
- Modify (test-author, re-pinned): `tests/test_health.py`, `tests/test_ip_reputation_tool.py`,
  `tests/test_tool_wiring_and_retry_trace.py` (+ `abuseipdb_quota_backoff_s` consumed),
  `tests/test_worker_main.py` (+ startup installs a `RedisTTLCache`), `tests/test_api_main.py`
  (+ `app.state.cache` is a `RedisTTLCache`), `tests/test_seed_dev.py` (3-tuples),
  `tests/test_env_example_roster.py` (nothing graduates; the new field must be documented)
- Modify: `core/cache.py`, `core/schemas/health.py`, `api/routes/health.py`, `api/main.py`,
  `worker/main.py`, `worker/tools/ip_reputation.py`, `worker/tools/wiring.py`, `core/config.py`,
  `.env.example`, `worker/triage_one.py`, `evals/run.py`, `scripts/seed_dev.py`,
  `alembic/env.py`, `api/openapi.json` + `web/src/types/generated/*` (the `HealthResponse`
  DTO gains `redis` — same commit), `SUGGESTIONS.md` (drop the two entries this task lands:
  the CLI helper and the 429 negative cache; fix-1 adds the once-per-transition WARNING idea),
  `README.md` (`/healthz` example body), `CONVENTIONS.md` (§4: the `/healthz` carve-out's Redis
  branch; §2: `cli.py`/`cache.py`/`queue.py` in the `core/` map — review M1/M4, PC1)

## Interfaces

- **Consumes:** `TTLCache`; `ArqRedis`/`Redis`; `app.state.redis`; `IpReputationTool`;
  `build_registry`; `HealthResponse`; `Settings`; `ConfigError`, `SentinelBriefError`.
- **Produces (M8's rate limiter and retriage cap rely on the cache shape — produce exactly):**

  ```python
  # core/config.py (+ .env.example line under "Enrichment tools (from M4)")
  abuseipdb_quota_backoff_s: Annotated[int, Field(ge=0)] = 900        # ABUSEIPDB_QUOTA_BACKOFF_S=900 — after a 429, skip AbuseIPDB for this long; 0 disables the back-off

  # core/cache.py
  REDIS_CACHE_KEY_PREFIX = "sentinelbrief:cache:"          # distinct name from worker/tools/ip_reputation.py's CACHE_KEY_PREFIX ("abuseipdb:")
  class RedisTTLCache:
      def __init__(self, redis: Redis, *, key_prefix: str = REDIS_CACHE_KEY_PREFIX) -> None
      async def get(self, key: str) -> bytes | None
          # await self._redis.get(key_prefix + key); RedisError | OSError -> logger.warning("redis cache get failed key=%s exc=%s", key, type(exc).__name__); return None
      async def set(self, key: str, value: bytes, ttl_s: int) -> None
          # ttl_s <= 0 -> ValueError("ttl_s must be > 0") (parity with InMemoryTTLCache — a programming error, not a Redis one)
          # await self._redis.set(key_prefix + key, value, px=ttl_s * 1000); RedisError | OSError -> logger.warning("redis cache set failed key=%s exc=%s", ...); return
      # Keys on the wire: api list/stats -> "sentinelbrief:cache:/api/v1/alerts?page=1&page_size=25" (the M3 cache_key verbatim);
      #                   reputation      -> "sentinelbrief:cache:abuseipdb:<canonical ip>"; quota flag -> "sentinelbrief:cache:abuseipdb:quota_exceeded".
      # Log lines carry the cache key and the exception class — never the Redis URL.

  # core/schemas/health.py
  class HealthResponse(BaseModel):
      status: Literal["ok", "degraded"]
      db: Literal["ok", "error", "unconfigured"]
      redis: Literal["ok", "error", "unconfigured"]

  # api/routes/health.py — the carve-out grows one branch; the two probes are independent (both run, both reported)
  #   db:    factory is None -> "unconfigured"; SELECT 1 raises SQLAlchemyError | OSError -> "error"; else "ok"
  #   redis: app.state.redis is None -> "unconfigured"; await redis.ping() raises RedisError | OSError -> "error"; else "ok"
  #   status: "ok" iff db == "ok" and redis == "ok" -> 200; else 503. Body always carries all three keys.

  # api/main.py — create_app(..., redis=redis_client)   — NO cache= kwarg: the api keeps the factory's bounded InMemoryTTLCache
  #   (review I2, ruling R14: a public route must never grow the shared Redis; 15 s / 60 s TTLs gain nothing from Redis; the
  #   briefing draft wired RedisTTLCache here and was reverted in fix-1)
  # worker/main.py::startup — cache = RedisTTLCache(ctx["redis"]); pipeline = TriagePipeline.from_settings(s, llm=llm, http=ctx["http"], cache=cache)
  #   (the ONLY RedisTTLCache consumer; bounded by distinct IPs × the 24 h TTL; no Redis maxmemory is configured on purpose —
  #   the instance also holds the ARQ queue and ARQ's job/result keys carry expiries, so volatile-lru could evict queued jobs)
  #   (ARQ has already put its pool at ctx["redis"] when on_startup runs — task-01's Context)

  # worker/tools/ip_reputation.py
  QUOTA_KEY = CACHE_KEY_PREFIX + "quota_exceeded"          # "abuseipdb:quota_exceeded" — the module's existing prefix constant + suffix; account-wide, no IP
  class IpReputationTool:
      def __init__(self, *, api_key, http, cache, cache_ttl_s, max_age_days, quota_backoff_s: int = 0) -> None    # quota_backoff_s < 0 -> ValueError
      async def run(...):
          # after the key check and AFTER the per-ip cache read (a warm entry costs no quota and is served even during the
          # back-off — review I1/PC2, ruling R14; the briefing draft had the flag BEFORE the read): if self._quota_backoff_s > 0 and
          # await self._cache.get(QUOTA_KEY) is not None: return unavailable("quota_exceeded")      (no HTTP call for a miss)
          # on a 429: if self._quota_backoff_s > 0: await self._cache.set(QUOTA_KEY, b"1", self._quota_backoff_s); return unavailable("quota_exceeded")
          # The per-ip success key is never written on any failure (unchanged). The tool still never wraps cache calls itself:
          # RedisTTLCache guarantees get/set never raise; InMemoryTTLCache raises only on a programming error (ttl <= 0).
  # worker/tools/wiring.py — IpReputationTool(..., quota_backoff_s=settings.abuseipdb_quota_backoff_s)

  # core/cli.py — the ONE copy of the CLI presentation helpers (moved verbatim; behaviour identical)
  class UsageError(SentinelBriefError): code = "usage"
  class Parser(argparse.ArgumentParser): def error(self, message) -> NoReturn: raise UsageError(f"{message} (see --help)")
  def fail(code: str, message: str) -> int      # print(f"error: {code}: {' '.join(message.split())}", file=sys.stderr); return 1
  # worker/triage_one.py, evals/run.py, scripts/seed_dev.py: delete their local UsageError/_Parser/_fail and import these
  # (scripts/fetch_geoip.py keeps its own error style — different exit-code contract; not touched)

  # alembic/env.py
  def _database_url() -> str:    # unchanged resolution order; an empty result -> raise ConfigError("DATABASE_URL is not set")

  # scripts/seed_dev.py
  def load_candidates(*, golden: Path, fixtures: Path) -> list[tuple[SessionAlert, str, tuple[str, ...]]]
      # fixtures first (sorted stem order) with FIXTURE_TOOL_TURNS.get(stem, ()), then golden rows with (); main() passes the triples straight to seed()
  ```

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| set/get/prefix/ttl | `tests/test_redis_cache.py::test_set_then_get_under_the_prefix_with_a_pttl` | `set("k", b"v", 15)`; `get("k") == b"v"`; `await arq_redis.get("sentinelbrief:cache:k") == b"v"`; `0 < await arq_redis.pttl("sentinelbrief:cache:k") <= 15_000` (no sleeping) |
| miss | `tests/test_redis_cache.py::test_get_miss_is_none` | unknown key → `None` |
| overwrite | `tests/test_redis_cache.py::test_set_overwrites_and_resets_the_ttl` | `set("k", b"a", 5)`; `set("k", b"b", 30)` → `get == b"b"`, `pttl > 5_000` |
| ttl bound | `tests/test_redis_cache.py::test_nonpositive_ttl_is_a_value_error` | `ttl_s=0` → `ValueError` (parity with `InMemoryTTLCache`) |
| dead redis: get | `tests/test_redis_cache.py::test_get_against_a_dead_redis_is_a_miss_with_one_warning` | `RedisTTLCache(make_redis("redis://:cache-pw-3@127.0.0.1:1/0", socket_timeout_s=0.5))` → `get("k") is None`; exactly one WARNING with `exc=ConnectionError` (or the concrete class) and `key=k`; `"cache-pw-3" not in caplog.text` |
| dead redis: set | `tests/test_redis_cache.py::test_set_against_a_dead_redis_is_a_noop_with_one_warning` | `set("k2", b"v", 5)` returns; one WARNING naming `key=k2`; `"cache-pw-3" not in caplog.text` (a different key than the get test — rule 5) |
| custom prefix | `tests/test_redis_cache.py::test_custom_key_prefix` | `key_prefix="t:"` → wire key `t:k` |
| healthz matrix | `tests/test_health.py::test_healthz_{unconfigured_both, ok_when_db_and_redis_answer, 503_when_redis_down, 503_when_db_down}` | DB-less `create_app()` → 503 `{"status": "degraded", "db": "unconfigured", "redis": "unconfigured"}`; `create_app(session_factory=db_session_factory, redis=arq_redis)` → 200 all ok; redis at `127.0.0.1:1` → 503 `{"…", "db": "ok", "redis": "error"}`; DB at `127.0.0.1:1` + real redis → 503 `{"db": "error", "redis": "ok"}` |
| ping timeout consumed | `tests/test_health.py::test_healthz_redis_error_answers_within_the_socket_timeout` | `make_redis("redis://10.255.255.1:6379/0", socket_timeout_s=0.5)` (a non-routable address: connect must time out, not be refused) → 503 within 3 s wall clock (`perf_counter`; generous bound, pins that the timeout is applied at all — rule 7) |
| api wiring (review I2, fix-1) | `tests/test_api_main.py::test_api_main_keeps_the_bounded_in_process_response_cache` | `isinstance(module.app.state.cache, InMemoryTTLCache)` and its bound equals `module.settings.alerts_cache_max_entries` (PRD §10.1: a public route never grows the shared Redis) |
| warm entry during back-off (review I1, fix-1) | `tests/test_ip_reputation_tool.py::test_warm_per_ip_entry_is_served_during_quota_backoff` | A cached (200), B arms the flag (429), A again → the cached payload with `cached: True`, handler count 2 |
| miss during back-off (fix-1) | `…::test_cache_miss_during_quota_backoff_makes_no_request` | C (never seen) → `quota_exceeded`, handler count still 2 |
| key check first (review M21, fix-1) | `…::test_no_api_key_beats_the_quota_flag` | unkeyed tool with the flag pre-seeded → `no_api_key`, zero requests |
| worker wiring | `tests/test_worker_main.py::test_startup_installs_a_redis_backed_reputation_cache` | `ctx["redis"] = arq_redis` before `startup(ctx)` → the reputation tool's `_cache` is a `RedisTTLCache` (private attribute, accepted pattern) |
| hit does no DB work | `tests/test_cache_hit_no_db.py::test_list_and_stats_cache_hits_check_out_no_connection` | `event.listen(db_engine.sync_engine, "checkout", counter)`; GET `/api/v1/alerts` twice and `/api/v1/stats` twice through `create_app(session_factory=…, cache=InMemoryTTLCache())`; checkouts after the first pair `== n`, after the second pair still `== n` (M3 N-M3). Mutation: delete `cache.get` in `_cached_json` → the count grows |
| quota back-off | `tests/test_ip_reputation_tool.py::test_429_sets_the_quota_flag_and_later_lookups_skip_the_network` | `quota_backoff_s=600`: ip A → handler returns 429 → `quota_exceeded`; ip B (different, rule 5) → `quota_exceeded` with the handler count still `1`; recording cache saw `set("abuseipdb:quota_exceeded", b"1", 600)` and NO set under `abuseipdb:<ip>` |
| back-off disabled | `tests/test_ip_reputation_tool.py::test_quota_backoff_zero_re_issues_the_request` | `quota_backoff_s=0`: A → 429, B → the handler is called again (count `2`); zero cache sets |
| back-off expiry | `tests/test_ip_reputation_tool.py::test_quota_flag_expires_with_the_clock` | `InMemoryTTLCache(clock=fake)`; after the 429 advance the clock by `600` → C issues a request |
| bounds | `tests/test_ip_reputation_tool.py::test_rejects_negative_quota_backoff` | `quota_backoff_s=-1` → `ValueError` |
| success cache untouched | `tests/test_ip_reputation_tool.py::test_failures_are_never_cached` | unchanged assertion, now with `quota_backoff_s=600`: zero sets under the ip prefix after a 429 (the quota key is asserted by name in the test above, so this pin keeps its meaning) |
| wiring consumes the setting | `tests/test_tool_wiring_and_retry_trace.py::test_build_registry_consumes_every_wired_settings_field` | + `abuseipdb_quota_backoff_s=321` → the tool's `_quota_backoff_s == 321` |
| settings | `tests/test_ip_reputation_tool.py::test_abuseipdb_settings_defaults_bounds_and_secret_repr` (re-pinned) | + default `900` (literal with comment); `abuseipdb_quota_backoff_s=-5` → `ValidationError`; roster green |
| CLI helper | `tests/test_cli_helper.py::test_parser_raises_usage_error_and_fail_prints_one_line` | `Parser(prog="x").parse_args(["--nope"])` → `UsageError` whose text ends with `(see --help)`; `fail("c", "a\nb  c")` → returns `1`, stderr exactly `error: c: a b c\n`; and the three CLIs: `grep -c "class _Parser\|def _fail\|class UsageError" worker/triage_one.py evals/run.py scripts/seed_dev.py` → `0` each (a subprocess-free assertion over file text — the behaviour pins are the existing CLI tests, unchanged) |
| alembic empty URL | `tests/test_alembic_env_url.py::test_upgrade_with_no_url_raises_config_error` | `monkeypatch.delenv("DATABASE_URL", "TEST_DATABASE_URL")`; `Config(alembic.ini)` with `sqlalchemy.url` set to `""` → `alembic.command.upgrade(cfg, "head")` raises `ConfigError` mentioning `DATABASE_URL` (no DB touched) |
| seed triples | `tests/test_seed_dev.py::test_load_candidates_returns_fixtures_then_golden` (re-pinned) | 25 triples; the first five are the sorted fixture stems with `FIXTURE_TOOL_TURNS[stem]` names; the golden rows carry `()`; `main()` no longer re-walks the fixtures directory to line the names up (`grep -n "fixture_stems\|tool_names_by_candidate" scripts/seed_dev.py` → nothing); the other 14 seed tests stay green unchanged |
| OpenAPI | (implementer) | `api/openapi.json` shows `redis` on `HealthResponse`; codegen regenerated; CI drift steps green |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2; the **implementer** does Steps 3–8.

- [ ] **Step 1 (RED — test-author): the four new files and the re-opened pins** per the table.
  The dead-Redis tests use three distinct fake passwords/keys (rules 5, 7).
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_redis_cache.py tests/test_health.py tests/test_cache_hit_no_db.py
  tests/test_ip_reputation_tool.py tests/test_cli_helper.py tests/test_alembic_env_url.py
  tests/test_seed_dev.py` → Expected: `ImportError: cannot import name 'RedisTTLCache'`; health
  bodies lack `redis`; `TypeError: unexpected keyword argument 'quota_backoff_s'`;
  `ModuleNotFoundError: core.cli`; the alembic test fails with SQLAlchemy's
  `ArgumentError`/`NoSuchModuleError` for an empty URL (not `ConfigError`); the seed test fails
  on 2-tuples. The no-DB-work pin is expected GREEN on arrival (it pins existing behaviour;
  mutation-proof it in the report: delete the `cache.get` line → fails). Pin, commit
  `test(api,core,worker): healthz redis, RedisTTLCache, quota back-off, cache-hit pin, CLI helper,
  alembic URL guard RED (m5 task-05)`.
- [ ] **Step 3 (GREEN — implementer): `core/cache.py::RedisTTLCache` + `core/config.py` +
  `.env.example`.**
- [ ] **Step 4 (GREEN — implementer): `/healthz`** (`core/schemas/health.py`,
  `api/routes/health.py`), `api/main.py` cache wiring, regenerate `api/openapi.json` + `pnpm -C
  web codegen` (same commit), README `/healthz` example.
- [ ] **Step 5 (GREEN — implementer): reputation quota back-off + wiring + `worker/main.py`
  startup cache.**
- [ ] **Step 6 (GREEN — implementer): carry-overs** — `core/cli.py` + the three CLIs,
  `alembic/env.py`, `scripts/seed_dev.py` triples; `SUGGESTIONS.md` loses the two landed entries.
  `uv run mypy` clean after each.
- [ ] **Step 7 (implementer): live** — restart the dev stack; `curl -s localhost:8000/healthz` →
  `{"status":"ok","db":"ok","redis":"ok"}`; `docker compose … stop redis`; `curl` → 503 with
  `"redis":"error"` and `docker compose … ps` shows `api` turning `unhealthy` within one
  healthcheck interval; `start redis` → healthy again. Paste (no secrets involved).
- [ ] **Step 8 (implementer): full gates (cold) → commit** `feat(api,core,worker): healthz redis
  ping, RedisTTLCache for response and reputation caches, quota back-off, CLI helper (m5
  task-05)` with the two trailers; path-scoped `git add`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_redis_cache.py tests/test_health.py tests/test_cache_hit_no_db.py tests/test_ip_reputation_tool.py tests/test_cli_helper.py tests/test_alembic_env_url.py tests/test_seed_dev.py tests/test_api_main.py tests/test_worker_main.py tests/test_tool_wiring_and_retry_trace.py tests/test_triage_one.py tests/test_evals_run.py   # all pass, 0 skipped
grep -c "class _Parser" worker/triage_one.py evals/run.py scripts/seed_dev.py      # 0 0 0
uv run python scripts/export_openapi.py --out /tmp/openapi.json && cmp /tmp/openapi.json api/openapi.json && pnpm -C web codegen && git diff --exit-code -- web/src/types/generated   # no drift
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
```

## Acceptance

- `/healthz` answers `200` only when Postgres and Redis both respond, `503 degraded` naming the
  failing side otherwise, within the configured socket timeout; the baseline and codegen moved
  with the DTO in the same commit.
- `RedisTTLCache` backs the api's list/stats cache and the worker's 24 h reputation cache under
  documented keys; a Redis failure is a miss/no-op with one WARNING and never a raise; a cache hit
  performs no database work (pinned).
- After an AbuseIPDB `429` no further request leaves the worker for `ABUSEIPDB_QUOTA_BACKOFF_S`
  (one account-wide key; `0` disables); success-only caching is unchanged.
- One copy of the CLI presentation helpers; `alembic upgrade` refuses an empty URL with a
  `ConfigError`; `seed_dev` computes the per-candidate tool names once.
