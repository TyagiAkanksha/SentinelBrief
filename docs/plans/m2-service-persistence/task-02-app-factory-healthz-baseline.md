---
id: task-02
milestone: m2-service-persistence
depends_on: [task-01]
status: planned
spec: PRD.md §8 (/healthz, envelope, operation_id, OpenAPI baseline), §10.5 (secrets), §12 M2; CONVENTIONS.md §4, §5, §7, §8
---

# task-02 — `create_app` factory, deps, error envelope, `/healthz`, `api/main.py`, OpenAPI baseline, env-roster test

## Goal

A DB-less constructible FastAPI app with the request-scoped seams on `app.state`
(`session_factory`, `settings`, `triage`), the §8 error envelope registered once, a root
`/healthz` that reports DB liveness (503 when degraded), a wiring-only `api/main.py` that fails
fast on empty required secrets, a committed `api/openapi.json` baseline with a drift test, and a
test that every `Settings` field is documented in `.env.example`.

## Context (read ONLY these)

- `PRD.md` §8, §10.5, §12 M2.
- `CONVENTIONS.md` §4 (errors, healthz carve-out), §5 (factory/main/operation_id), §7, §8.
- `.claude/rules/api.md`.
- `core/db.py`, `core/models/alerts.py` (task-01); `core/errors.py`, `core/config.py` (M0).

## Files

- Create: `api/factory.py`, `api/main.py`, `api/deps.py`, `api/errors.py`,
  `api/routes/__init__.py`, `api/routes/health.py`, `api/openapi.json`,
  `scripts/export_openapi.py`
- Create: `tests/test_app_factory.py`, `tests/test_health.py`, `tests/test_error_envelope.py`,
  `tests/test_openapi_baseline.py`, `tests/test_env_example_roster.py`
- Modify: `core/errors.py` (+ `SignatureError`, `NotFoundError`, `ConflictError`,
  `RateLimitedError`), `core/config.py` (+ `ingest_hmac_secret: SecretStr`, `cors_origins: str
  = "http://localhost:3000"`, `cors_origin_list` property), `pyproject.toml` (deps `fastapi`,
  `uvicorn`), `.github/workflows/ci.yml` (baseline drift step)

## Interfaces

- **Consumes:** `make_engine`, `make_session_factory`, `AlertStatus`, `Settings`,
  `SentinelBriefError` family, `OpenAICompatibleLLMClient` (wired in task-04, not here).
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/errors.py additions
  class SignatureError(SentinelBriefError): code = "unauthorized"
  class NotFoundError(SentinelBriefError): code = "not_found"
  class ConflictError(SentinelBriefError): code = "conflict"
  class RateLimitedError(SentinelBriefError): code = "rate_limited"

  # api/deps.py
  TriageFn = Callable[[AsyncSession, uuid.UUID], Awaitable[AlertStatus]]
  def get_settings(request: Request) -> Settings: ...
  async def get_session(request: Request) -> AsyncIterator[AsyncSession]: ...
      # factory = request.app.state.session_factory; None -> RuntimeError("no session_factory wired");
      # async with factory() as session: try: yield session; await session.commit() except: await session.rollback(); raise
  def get_triage(request: Request) -> TriageFn: ...        # RuntimeError("no triage wired") when None

  # api/errors.py
  STATUS_BY_ERROR: Mapping[type[SentinelBriefError], int] = {SignatureError: 401, NotFoundError: 404, ConflictError: 409,
      RateLimitedError: 429, ConfigError: 500, LLMCallError: 502, VerdictValidationError: 502, StructuredOutputError: 502}
  def register_error_handlers(app: FastAPI) -> None: ...
      # SentinelBriefError -> JSONResponse(status, {"error": {"code": exc.code, "message": str(exc)}});
      # RequestValidationError -> 422 {"error": {"code": "validation_error", "message": "<loc>: <msg>; ..."}} (never the input);
      # Exception -> 500 {"error": {"code": "internal_error", "message": "internal error"}} + logger.exception

  # api/factory.py
  def create_app(*, session_factory: async_sessionmaker[AsyncSession] | None = None,
                 settings: Settings | None = None, triage: TriageFn | None = None) -> FastAPI: ...
      # app.state.{settings, session_factory, triage}; CORSMiddleware(allow_origins=settings.cors_origin_list); register_error_handlers;
      # include health router at root; include api routers under "/api/v1" (alerts router arrives in task-03)

  # api/routes/health.py — GET /healthz, operation_id="healthz":
  #   no factory -> 503 {"status":"degraded","db":"unconfigured"}; SELECT 1 ok -> 200 {"status":"ok","db":"ok"};
  #   SQLAlchemyError | OSError -> 503 {"status":"degraded","db":"error"}   (documented try/except carve-out)

  # api/main.py — wiring only: _configure_logging(); settings = Settings(); _require_nonempty(DATABASE_URL, INGEST_HMAC_SECRET);
  #   engine = make_engine(...); session_factory = make_session_factory(engine); app = create_app(session_factory=..., settings=...)
  #   (LLM_API_KEY/CHEAP_MODEL guards + triage wiring are added by task-04)

  # scripts/export_openapi.py — writes json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n" to api/openapi.json
  ```

## Steps (TDD)

- [ ] **Step 1: Write failing tests.** `tests/test_app_factory.py`:
  `test_create_app_without_db_or_env_builds`, `test_operation_ids_unique` (walk `app.routes`),
  `test_get_session_raises_when_dbless` (a probe route using `Depends(get_session)` → the
  `RuntimeError` surfaces as the 500 envelope). `tests/test_health.py`:
  `test_healthz_503_when_db_unconfigured`, `test_healthz_ok_pings_db` (`db_session_factory`),
  `test_healthz_503_when_db_down` (a factory bound to an engine on a closed port).
  `tests/test_error_envelope.py`: `test_signature_error_maps_to_401_envelope` (probe route
  raising `SignatureError`), `test_422_enveloped_without_input_echo` (post `{"x": "SECRET-INPUT"}`
  to a probe route with a typed body → body lacks `SECRET-INPUT`),
  `test_unhandled_exception_500_enveloped`. `tests/test_openapi_baseline.py::test_committed_baseline_matches_app`.
  `tests/test_env_example_roster.py::test_every_settings_field_documented_in_env_example`
  (every `Settings.model_fields` name upper-cased appears as `NAME=` or `# NAME=` in
  `.env.example`). All HTTP tests use `httpx.AsyncClient(transport=ASGITransport(app=app),
  base_url="http://test")`.
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: api.factory`.
- [ ] **Step 3: Implement errors, deps, factory, health route, main.**
- [ ] **Step 4: `uv run python scripts/export_openapi.py`; commit the baseline; add the CI drift step
  (`git diff --exit-code -- api/openapi.json` after re-export).**
- [ ] **Step 5: Tests pass (export line set), `uv run mypy` clean, `lint-imports` 5 kept.**
- [ ] **Step 6: Full gates → commit:**
  `feat(api): create_app factory, error envelope, /healthz, OpenAPI baseline (m2 task-02)`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief_test
uv run pytest -q tests/test_app_factory.py tests/test_health.py tests/test_error_envelope.py tests/test_openapi_baseline.py tests/test_env_example_roster.py   # 11 passed
uv run python scripts/export_openapi.py && git diff --exit-code -- api/openapi.json          # no drift
DATABASE_URL=$TEST_DATABASE_URL INGEST_HMAC_SECRET=x uv run uvicorn api.main:app --port 8000 &  # boots; curl localhost:8000/healthz -> {"status":"ok","db":"ok"}
```

## Acceptance

- `create_app()` builds with nothing wired; `/healthz` answers 200/503 correctly; every error
  path returns the §8 envelope and never echoes input.
- `api/openapi.json` is committed and drift-checked; `.env.example` documents every setting.
