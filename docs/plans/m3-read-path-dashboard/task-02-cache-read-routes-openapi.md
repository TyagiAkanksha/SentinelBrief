---
id: task-02
milestone: m3-read-path-dashboard
depends_on: [task-01]
status: planned
spec: PRD.md §8 (GET list/detail/stats, cache TTLs 15 s / 60 s, error envelope, operation_id, OpenAPI baseline, /healthz), §10.1 (public paths never compute), §12 M3; CONVENTIONS.md §4, §5, §7, §8, §10 (the clock seam)
---

# task-02 — `core/cache.py` TTL cache seam, unsigned read router (`list_alerts`, `get_alert`, `get_stats`), error-handler MRO/≥500 hardening, OpenAPI hygiene, `export_openapi.py --out`, CI drift step, `SessionDep` structural test

## Goal

`GET /api/v1/alerts`, `GET /api/v1/alerts/{alert_id}` and `GET /api/v1/stats` exist on their own
**unsigned** router, answer from the task-01 services and the database only (never the LLM), and
the list/stats responses are served from an in-process TTL cache (15 s / 60 s from settings)
behind a `TTLCache` Protocol that M5 swaps for Redis in one wiring line. `api/errors.py` resolves
statuses by walking the exception's MRO, hides the message of every ≥ 500 mapping behind
`"internal error"` (logging the real one), and builds every envelope from `ErrorEnvelope`. The
OpenAPI baseline gets the hygiene codegen needs — title/version, `\f`-truncated descriptions, a
`HealthResponse` model, `ErrorEnvelope` on every error response, stable `operation_id`s — and is
regenerated; `scripts/export_openapi.py` gains `--out` so the determinism test stops rewriting the
tracked file; CI diffs the export explicitly; a structural test forbids bare
`Depends(get_session)` in any route module. (M2 final review, plan defects 3, 4, 5, 7, 10, 12.)

## Context (read ONLY these)

- `PRD.md` §8, §10.1, §12 M3.
- `docs/plans/m3-read-path-dashboard.md` — Global Constraints (unsigned router, `SessionDep`,
  real coverage, CORS untouched, nothing triggers compute).
- `CONVENTIONS.md` §4 (errors), §5 (factory, `operation_id`, `/api/v1`), §7 (settings +
  `.env.example`), §8 (baseline), §10 (the clock is an injectable seam).
- `.claude/rules/api.md`, `.claude/rules/core.md`, `.claude/rules/tests.md`.
- Code you build on: `api/factory.py`, `api/deps.py` (`SessionDep`, `get_settings`),
  `api/errors.py`, `api/routes/health.py`, `api/routes/alerts.py` (the signed router — read only,
  never add to it), `api/openapi.json`, `core/config.py`, `core/errors.py`,
  `scripts/export_openapi.py`, `.github/workflows/ci.yml`, `.env.example`, `pyproject.toml`
  (import-linter contract 1), `tests/test_openapi_baseline.py`, `tests/test_error_envelope.py`,
  `tests/test_app_factory.py` (`test_operation_ids_unique` already covers the new routes),
  `tests/test_post_alert.py` (the `importlib.util.spec_from_file_location` pattern for importing a
  script), `tests/helpers.py` (task-01).
- Task-01 outputs: `core/schemas/{pagination,errors,alerts_read}.py`,
  `core/services/alerts_read.py`.

## Files

- Create: `core/cache.py`, `core/schemas/health.py`, `api/routes/alerts_read.py`
- Create (test-author): `tests/test_cache.py`, `tests/test_read_routes.py`,
  `tests/test_error_status_mapping.py`, `tests/test_openapi_hygiene.py`
- Modify (test-author, re-pinned): `tests/test_openapi_baseline.py` (rewrite
  `test_export_script_is_deterministic` to use `--out`, add
  `test_export_script_default_out_is_the_tracked_baseline`), `tests/test_error_envelope.py`
  (`test_config_error_maps_to_500_and_llm_errors_to_502`: the message assertion becomes
  `== "internal error"` for every ≥ 500 row, with a comment citing M2 final review M3)
- Modify: `api/deps.py` (+ `get_cache`), `api/factory.py` (`cache` kwarg, title/version, include
  the read router), `api/errors.py`, `api/routes/health.py`, `api/routes/alerts.py` (docstring
  `\f` + `responses` models only — the router and the route body are untouched),
  `core/config.py` (+ two TTL settings), `.env.example` (+ two lines), `scripts/export_openapi.py`,
  `api/openapi.json` (regenerated), `.github/workflows/ci.yml` (explicit drift step),
  `pyproject.toml` (contract 1 `forbidden_modules` += `core.cache`)

## Interfaces

- **Consumes:** `PaginatedResponse`, `ErrorEnvelope`, `ErrorBody`, `AlertSummary`, `AlertDetail`,
  `StatsOut`, `ListFilters`, `list_alerts`, `get_alert_detail`, `get_stats` (task-01);
  `SessionDep`, `get_settings`, `Settings`, `SentinelBriefError` family, `create_app`,
  `API_V1_PREFIX`; `tests.helpers.seed_alert` / `add_verdict` / `count_rows`; `VerdictCategory`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/cache.py — no project imports (a pure seam; M5 adds RedisTTLCache beside it)
  class TTLCache(Protocol):
      async def get(self, key: str) -> bytes | None: ...
      async def set(self, key: str, value: bytes, ttl_s: int) -> None: ...
  class InMemoryTTLCache:
      def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None: ...
          # _entries: dict[str, tuple[float, bytes]]  (expires_at, value)
      async def get(self, key: str) -> bytes | None: ...     # None (and evict) when clock() >= expires_at
      async def set(self, key: str, value: bytes, ttl_s: int) -> None: ...   # ttl_s <= 0 -> ValueError("ttl_s must be > 0"); overwrites
  # The methods are `async` even though the in-memory version never awaits: a Redis client is
  # async, and a sync Protocol would force M5 to change the Protocol, both routes and every route
  # test — not "one wiring line". Deliberate deviation from the briefing's sync sketch, recorded in
  # the briefing report's open questions.

  # core/config.py additions (+ .env.example lines, under "API / web (from M2 / M3)")
  alerts_list_cache_ttl_s: int = 15      # ALERTS_LIST_CACHE_TTL_S=15   — PRD §8 "Cached 15 s"
  stats_cache_ttl_s: int = 60            # STATS_CACHE_TTL_S=60         — PRD §8 "Cached 60 s"

  # core/schemas/health.py
  class HealthResponse(BaseModel):
      status: Literal["ok", "degraded"]
      db: Literal["ok", "error", "unconfigured"]

  # api/deps.py addition
  def get_cache(request: Request) -> TTLCache: ...        # request.app.state.cache — create_app always installs one, never None

  # api/factory.py
  def create_app(*, session_factory: async_sessionmaker[AsyncSession] | None = None,
                 settings: Settings | None = None, triage: TriageFn | None = None,
                 cache: TTLCache | None = None) -> FastAPI: ...
      # app = FastAPI(title="SentinelBrief API", version=importlib.metadata.version("sentinelbrief"))
      # app.state.cache = cache if cache is not None else InMemoryTTLCache()
      # app.include_router(alerts_read_router, prefix=API_V1_PREFIX)   — after alerts_router; CORS wiring unchanged (M3 never touches CORS)

  # api/errors.py
  GENERIC_MESSAGE = "internal error"
  STATUS_BY_ERROR: Mapping[type[SentinelBriefError], int]   # rows unchanged from M2
  def status_for(exc_type: type[SentinelBriefError]) -> int | None: ...
      # first STATUS_BY_ERROR hit walking exc_type.__mro__; None when no ancestor is mapped
  def _envelope(status_code: int, code: str, message: str) -> JSONResponse: ...
      # JSONResponse(status_code, content=ErrorEnvelope(error=ErrorBody(code=code, message=message)).model_dump())
      # — the ONLY constructor of an error body; the 422 and unhandled-500 handlers use it too
  # _handle_sentinelbrief_error: status = status_for(type(exc)) or 500;
  #   message = str(exc) if status < 500 else GENERIC_MESSAGE;
  #   status >= 500 -> logger.error("sentinelbrief error code=%s status=%s message=%s", exc.code, status, exc)
  #   the wire `code` stays exc.code (e.g. "llm_call_failed" at 502) — only the message is generic

  # api/routes/health.py — GET /healthz, operation_id="healthz", response_model=HealthResponse,
  #   responses={503: {"model": HealthResponse, "description": "Degraded: database unconfigured or unreachable."}}
  #   bodies unchanged: {"status":"ok","db":"ok"} / {"status":"degraded","db":"unconfigured"|"error"}; docstring summary ends with \f

  # api/routes/alerts_read.py — unsigned: `router = APIRouter()` (never route_class=SignedRoute;
  #   PRD §8 marks these routes public and the signed router holds only POST /alerts)
  router = APIRouter()
  # imports — the route functions keep the operation_id names, so the services are aliased:
  # from core.services.alerts_read import get_alert_detail
  # from core.services.alerts_read import get_stats as get_stats_service
  # from core.services.alerts_read import list_alerts as list_alerts_service
  def cache_key(request: Request) -> str: ...
      # f"{request.url.path}?{urlencode(sorted(request.query_params.multi_items()))}"  -> normalized full query
  async def _cached_json(request: Request, cache: TTLCache, ttl_s: int,
                         produce: Callable[[], Awaitable[BaseModel]]) -> Response: ...
      # key = cache_key(request); hit -> Response(content=hit, media_type="application/json")
      # miss -> model = await produce(); body = model.model_dump_json().encode();
      #         await cache.set(key, body, ttl_s); Response(content=body, media_type="application/json")
      # only a successful produce() reaches cache.set -> non-2xx is never cached by construction

  @router.get("/alerts", operation_id="list_alerts", response_model=PaginatedResponse[AlertSummary],
              responses={422: {"model": ErrorEnvelope}, 500: {"model": ErrorEnvelope}})
  async def list_alerts(request: Request, session: SessionDep,
                        settings: Settings = Depends(get_settings), cache: TTLCache = Depends(get_cache),
                        page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                        severity_gte: int | None = Query(None, ge=1, le=5),
                        category: VerdictCategory | None = Query(None),
                        since: datetime | None = Query(None),
                        escalate: bool | None = Query(None)) -> Response: ...
      # filters = ListFilters(severity_gte=..., category=..., since=..., escalate=...)
      # produce: items, total = await list_alerts_service(session, filters=filters, page=page, page_size=page_size)
      #          -> PaginatedResponse[AlertSummary](items=items, total=total, page=page, page_size=page_size)
      # ttl_s = settings.alerts_list_cache_ttl_s
  @router.get("/alerts/{alert_id}", operation_id="get_alert", response_model=AlertDetail,
              responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}, 500: {"model": ErrorEnvelope}})
  async def get_alert(alert_id: uuid.UUID, session: SessionDep) -> AlertDetail: ...
      # return await get_alert_detail(session, alert_id)  — NOT cached (PRD §8 caches list and stats only); NotFoundError -> 404 envelope
  @router.get("/stats", operation_id="get_stats", response_model=StatsOut,
              responses={500: {"model": ErrorEnvelope}})
  async def get_stats(request: Request, session: SessionDep,
                      settings: Settings = Depends(get_settings), cache: TTLCache = Depends(get_cache)) -> Response: ...
      # ttl_s = settings.stats_cache_ttl_s
  # Every docstring: one summary paragraph, then \f, then Args:/Returns: — FastAPI truncates the
  # OpenAPI description at the form feed. No try/except anywhere in this module.

  # api/routes/alerts.py — docstring gains \f after its summary; responses become
  #   {200: {"model": IngestResponse, ...unchanged description}, 401: {"model": ErrorEnvelope, ...}, 422: {"model": ErrorEnvelope, ...}, 500: {"model": ErrorEnvelope}}

  # scripts/export_openapi.py
  DEFAULT_OUT: Path = <repo>/api/openapi.json
  def render_openapi() -> str: ...          # json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
  def main(argv: Sequence[str] | None = None) -> int: ...
      # argparse: --out PATH (default DEFAULT_OUT); mkdir parents; write_text(render_openapi()); return 0

  # .github/workflows/ci.yml — python job, after "import-linter", before pytest:
  #   - name: OpenAPI baseline drift (CONVENTIONS §8)
  #     run: uv run python scripts/export_openapi.py --out /tmp/openapi.json && cmp /tmp/openapi.json api/openapi.json
  ```

  Baseline facts the implementer pastes into its report after regenerating: the exact
  `components.schemas` key for the paginated list (expected `PaginatedResponse_AlertSummary_` —
  verified at briefing time on FastAPI 0.141.1 / pydantic 2.13.5), and the three `operationId`s.
  Task-03 consumes the names from `api/openapi.json`, never from prose.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `InMemoryTTLCache.get` miss | `tests/test_cache.py::test_in_memory_cache_miss_returns_none` | unknown key → `None` |
| `InMemoryTTLCache` hit | `tests/test_cache.py::test_in_memory_cache_hit_before_expiry` | clock at t+14, ttl 15 → bytes returned unchanged |
| expiry boundary | `tests/test_cache.py::test_in_memory_cache_expires_at_ttl_boundary` | clock at exactly t+15 → `None`; the entry is evicted (a later `set` is not required to clear it) |
| `set` overwrites | `tests/test_cache.py::test_in_memory_cache_set_overwrites_value_and_ttl` | second `set` replaces bytes and restarts the TTL |
| `ttl_s <= 0` | `tests/test_cache.py::test_in_memory_cache_rejects_non_positive_ttl` | `0` and `-1` → `ValueError` |
| `create_app` default cache | `tests/test_cache.py::test_create_app_installs_in_memory_cache_by_default` | `isinstance(app.state.cache, InMemoryTTLCache)` |
| `get_cache` | `tests/test_cache.py::test_get_cache_returns_injected_instance` | probe route with `Depends(get_cache)` returns the injected object's `id()` |
| `GET /alerts` envelope | `tests/test_read_routes.py::test_list_alerts_returns_paginated_envelope` | keys `items,total,page,page_size`; item has `src_ip`, `verdict.severity`, `verdict.reasoning_excerpt` |
| filters wired | `tests/test_read_routes.py::test_list_alerts_filters_are_wired` (parametrized: `severity_gte=4`, `category=brute_force`, `escalate=true`, `since=<T+30m>`) | each query string yields the expected `total` against a 3-alert seed |
| query validation | `tests/test_read_routes.py::test_list_alerts_422_envelope_for_invalid_query` (parametrized: `page=0`, `page_size=101`, `severity_gte=6`, `category=bogus`, `since=yesterday`) | 422 with `error.code == "validation_error"`, body keys exactly `{"error"}` |
| list TTL from settings | `tests/test_read_routes.py::test_list_alerts_cached_until_alerts_list_cache_ttl_s` | `Settings(alerts_list_cache_ttl_s=7)` + injected clock: seed → GET (total 1) → seed → GET at +6 (still 1) → GET at +7 (2) |
| cache key normalization | `tests/test_read_routes.py::test_list_alerts_cache_key_normalizes_query_param_order` | `?page=1&page_size=5` then seed then `?page_size=5&page=1` → stale total (same key) |
| cached content-type | `tests/test_read_routes.py::test_list_alerts_cache_hit_keeps_json_content_type` | second response `content-type` starts with `application/json` and parses |
| never cache non-2xx | `tests/test_read_routes.py::test_list_alerts_non_2xx_never_cached` | `?page=0` → 422; `await cache.get("/api/v1/alerts?page=0") is None` |
| `GET /alerts/{id}` | `tests/test_read_routes.py::test_get_alert_returns_detail_with_verdict_and_tool_calls` | `raw`, `verdict.reasoning`, `tool_calls[*].seq == [0, 1]` |
| 404 envelope | `tests/test_read_routes.py::test_get_alert_404_envelope` | `uuid4()` → `{"error": {"code": "not_found", "message": ...}}` |
| non-UUID path | `tests/test_read_routes.py::test_get_alert_422_envelope_for_non_uuid` | `/api/v1/alerts/not-a-uuid` → 422 envelope |
| detail uncached | `tests/test_read_routes.py::test_get_alert_is_never_cached` | GET → `add_verdict` (sev 5) + commit → GET again shows severity 5 |
| `GET /stats` | `tests/test_read_routes.py::test_get_stats_returns_stats_out` | every `StatsOut` key present; `total_alerts` matches the seed |
| stats TTL from settings | `tests/test_read_routes.py::test_get_stats_cached_until_stats_cache_ttl_s` | `Settings(stats_cache_ttl_s=3)`: stale at +2, fresh at +3 |
| unsigned router | `tests/test_read_routes.py::test_read_router_is_unsigned` | `type(alerts_read.router) is APIRouter`; no route `isinstance(SignedRoute)`; GET without `X-Signature` is 200 |
| no bare `Depends(get_session)` | `tests/test_read_routes.py::test_no_route_uses_bare_get_session_dependency` | grep of `api/routes/*.py` for the literal `Depends(get_session)` finds nothing |
| `status_for` MRO walk | `tests/test_error_status_mapping.py::test_status_for_walks_the_mro` | `class _Gone(NotFoundError)` → 404 via `status_for` and via a probe route (`code == "not_found"`) |
| every concrete error mapped | `tests/test_error_status_mapping.py::test_every_concrete_error_has_a_status` | walk `core.errors` classes that subclass `SentinelBriefError` (base excluded) → `status_for(cls) is not None` |
| unmapped → `None` | `tests/test_error_status_mapping.py::test_status_for_unmapped_returns_none` | a direct `SentinelBriefError` subclass with no row → `None`; probe route → 500 generic |
| ≥ 500 generic message | `tests/test_error_status_mapping.py::test_5xx_mapped_errors_use_generic_message_and_log_the_real_one` (parametrized `ConfigError`→500, `LLMCallError`→502) | body message `"internal error"`, real text absent from the body, present in `caplog` at ERROR; `code` stays the class code |
| < 500 keeps message | `tests/test_error_status_mapping.py::test_4xx_mapped_errors_keep_their_message` | `ConflictError("dup")` → message `"dup"` |
| envelopes from `ErrorEnvelope` | `tests/test_error_status_mapping.py::test_every_handler_output_validates_as_error_envelope` (parametrized 401, 404, 422, unhandled 500) | `ErrorEnvelope.model_validate(response.json())` succeeds and `model_dump()` round-trips the body |
| title/version | `tests/test_openapi_hygiene.py::test_info_title_and_version` | `info.title == "SentinelBrief API"`, `info.version == importlib.metadata.version("sentinelbrief")` |
| `\f` truncation | `tests/test_openapi_hygiene.py::test_no_operation_description_contains_docstring_sections` | no operation `description` contains `Args:`, `Returns:` or `Raises:` |
| healthz model | `tests/test_openapi_hygiene.py::test_healthz_declares_health_response_for_200_and_503` | both responses `$ref` `HealthResponse` |
| error responses | `tests/test_openapi_hygiene.py::test_every_error_response_references_error_envelope` | for every operation except `healthz`, every response with status ≥ 400 has schema `$ref` `#/components/schemas/ErrorEnvelope` |
| stable generic name | `tests/test_openapi_hygiene.py::test_paginated_alert_summary_schema_name_is_stable` | `"PaginatedResponse_AlertSummary_" in components.schemas` and `/api/v1/alerts` GET 200 refs it |
| operation ids | `tests/test_openapi_hygiene.py::test_read_operation_ids_present` | `{"list_alerts", "get_alert", "get_stats"} ⊆ operationIds` |
| `--out` determinism | `tests/test_openapi_baseline.py::test_export_script_is_deterministic` (re-pinned) | two runs into `tmp_path/a.json` and `b.json` are byte-equal and equal the committed file; the tracked file is never written |
| default out path | `tests/test_openapi_baseline.py::test_export_script_default_out_is_the_tracked_baseline` | `module.DEFAULT_OUT == REPO_ROOT / "api" / "openapi.json"` (loaded via `spec_from_file_location`) |
| baseline matches app | `tests/test_openapi_baseline.py::test_committed_baseline_matches_app` (unchanged) | regenerated baseline committed |
| settings roster | `tests/test_env_example_roster.py` (existing, unchanged) | both new fields documented in `.env.example` |
| CI drift step | task-03's `tests/test_web_scaffold_pins.py::test_ci_has_web_job_with_codegen_drift_step` also asserts the `export_openapi.py --out /tmp/openapi.json && cmp` step exists | — |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins every file it created or edited; the
**implementer** does Steps 3–7 and never edits a pinned file (it stops and asks first).

- [ ] **Step 1 (RED — test-author): write the four new test files and re-pin the two M2 files**
  per the table. Route tests build `create_app(session_factory=db_session_factory,
  settings=Settings(ingest_hmac_secret=SecretStr("test-secret"), alerts_list_cache_ttl_s=7,
  stats_cache_ttl_s=3), cache=InMemoryTTLCache(clock=fake_clock))` where `fake_clock` is a
  closure over a mutable float the test advances; seed with `tests.helpers.seed_alert` and commit
  before each GET. The structural test reads every `api/routes/*.py` as text.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q tests/test_cache.py
  tests/test_read_routes.py tests/test_error_status_mapping.py tests/test_openapi_hygiene.py
  tests/test_openapi_baseline.py tests/test_error_envelope.py` → Expected: `ModuleNotFoundError:
  No module named 'core.cache'` (test_cache, test_read_routes), `ImportError: cannot import name
  'status_for'` (test_error_status_mapping), assertion failures on title `"FastAPI"` and on
  `Args:` in descriptions (test_openapi_hygiene), `error: unrecognized arguments: --out`
  (test_openapi_baseline), and the `"internal error"` message assertion (test_error_envelope).
  Pin, commit `test(api): cache seam, read routes, error mapping, OpenAPI hygiene RED (m3 task-02)`.
- [ ] **Step 3 (GREEN — implementer): `core/cache.py`, `core/schemas/health.py`, the two
  `Settings` fields + `.env.example` lines, `api/deps.py::get_cache`, `create_app(cache=...)` with
  title/version.** `uv run mypy` clean.
- [ ] **Step 4 (GREEN — implementer): `api/errors.py`** (`status_for`, `_envelope`, generic ≥ 500
  message + log); `api/routes/health.py` (`HealthResponse`, `\f`); `api/routes/alerts.py`
  (`\f`, `responses` models). `uv run mypy` clean.
- [ ] **Step 5 (GREEN — implementer): `api/routes/alerts_read.py`** per Interfaces; include it in
  the factory. `uv run mypy` clean; `uv run pytest -q tests/test_read_routes.py` green.
- [ ] **Step 6 (implementer): `scripts/export_openapi.py --out`; regenerate `api/openapi.json`;
  add the CI drift step; add `core.cache` to contract 1's `forbidden_modules`.** Paste into the
  report: the `components.schemas` keys and the `operationId` list from the regenerated file.
- [ ] **Step 7 (implementer): full gates → commit:**
  `feat(api): unsigned read routes with TTL cache, error-handler hardening, OpenAPI hygiene (m3 task-02)`
  with the two trailers. Path-scoped `git add` (include `api/openapi.json`, `.env.example`,
  `.github/workflows/ci.yml`, `pyproject.toml`).

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_cache.py tests/test_read_routes.py tests/test_error_status_mapping.py tests/test_openapi_hygiene.py tests/test_openapi_baseline.py tests/test_error_envelope.py tests/test_health.py tests/test_app_factory.py tests/test_env_example_roster.py   # 68 passed
uv run python scripts/export_openapi.py --out /tmp/openapi.json && cmp /tmp/openapi.json api/openapi.json && echo no-drift   # no-drift
python3 -c "import json; s=json.load(open('api/openapi.json')); print(s['info']['title'], sorted(k for k in s['components']['schemas'] if k.startswith('Paginated')))"   # SentinelBrief API ['PaginatedResponse_AlertSummary_']
grep -rn "Depends(get_session)" api/routes/ ; echo "exit=$?"   # exit=1 (no hits)
grep -c "import worker\|from worker\|core.llm\|try:" api/routes/alerts_read.py ; echo "exit=$?"   # 0 / exit=1 (no LLM reach, no try/except)
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean; "Contracts: 5 kept, 0 broken"
```

## Acceptance

- `GET /api/v1/alerts`, `/alerts/{id}`, `/stats` answer from the DB and the cache only; list and
  stats are cached for exactly `ALERTS_LIST_CACHE_TTL_S` / `STATS_CACHE_TTL_S` seconds under a
  normalized full-query key, cache hits keep `application/json`, non-2xx is never cached, detail
  is never cached; 404/422 use the §8 envelope.
- Every `SentinelBriefError` subclass resolves through the MRO; ≥ 500 mappings never leak their
  message; every envelope is an `ErrorEnvelope`.
- `api/openapi.json` carries the title/version, no `Args:`/`Returns:` leakage, `HealthResponse`,
  `ErrorEnvelope` on every error response, and the stable names `list_alerts` / `get_alert` /
  `get_stats` / `PaginatedResponse_AlertSummary_`; CI diffs a fresh `--out` export against it.
