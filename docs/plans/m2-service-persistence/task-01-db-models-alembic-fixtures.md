---
id: task-01
milestone: m2-service-persistence
depends_on: []
status: planned
spec: PRD.md §5 (schema + indexes, verbatim), §4 (async SQLAlchemy on psycopg 3); CONVENTIONS.md §6, §7, §10
---

# task-01 — Async DB layer, ORM rows, Alembic 0001, throwaway-schema test fixtures, CI Postgres

## Goal

`core/db.py` builds an async engine on psycopg 3 with an optional `search_path`; the four PRD §5
tables exist as `Row` ORM classes (pure leaf) and as Alembic migration `0001_initial_schema` with
the §5 indexes; `tests/conftest.py` gives every DB test its own migrated throwaway schema and
skips by fixture name when `TEST_DATABASE_URL` is unset; CI runs a Postgres service.

## Context (read ONLY these)

- `PRD.md` §5 (every column, type, constraint and index — normative), §4 (stack).
- `CONVENTIONS.md` §6 (2.0 style, `Row` suffix, Alembic-only DDL, async env.py), §7
  (`database_url`), §10 (fixtures: sync `tmp_schema`, skip-by-name).
- `.claude/rules/{core,tests}.md`.
- AdvisorDesk `apps/api/app/db.py`, `alembic/env.py`, `tests/conftest.py` (read-only) — the
  sync shapes to adapt.

## Files

- Also: dev dep `pytest-timeout` with `timeout = 120` in `[tool.pytest.ini_options]` (a hung DB test must fail, never wedge CI — M1 review carry-over).

- Create (packages — `core/models/` keeps its `__init__.py`; every symbol below names its file): `core/db.py`, `core/models/base.py`, `core/models/alerts.py`,
  `core/models/verdicts.py`, `core/models/tool_calls.py`, `core/models/eval_runs.py`,
  `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
  `alembic/versions/0001_initial_schema.py`
- Create: `tests/test_db.py`, `tests/test_models_schema.py`, `tests/test_alembic_roundtrip.py`
- Modify: `core/models/__init__.py` (re-export `Base`, the four rows, `AlertStatus`),
  `core/config.py` (+ `database_url: SecretStr = SecretStr("")`), `pyproject.toml` (deps
  `sqlalchemy[asyncio]>=2`, `psycopg[binary]>=3`, `alembic`), `tests/conftest.py`,
  `.github/workflows/ci.yml` (Postgres service + `TEST_DATABASE_URL`)

## Interfaces

- **Consumes:** `Settings` (M0).
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/db.py
  def make_engine(database_url: str, *, schema: str | None = None) -> AsyncEngine: ...
      # make_url; drivername "postgresql" -> "postgresql+psycopg"; connect_args={"options": f"-csearch_path={schema},public"} when schema
  def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]: ...   # expire_on_commit=False

  # core/models/base.py
  class Base(DeclarativeBase): ...
  def uuid_pk() -> Mapped[uuid.UUID]: ...        # mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))

  # core/models/alerts.py
  AlertStatus = Literal["pending", "triaged", "failed"]
  class AlertRow(Base):                          # __tablename__ = "alerts"
      id; fingerprint: Mapped[str] (unique, index); source: Mapped[str]; event_time: Mapped[datetime] (timestamptz)
      received_at: Mapped[datetime] (server_default=now()); raw: Mapped[dict[str, Any]] (JSONB); status: Mapped[str] (server_default="pending")
  # core/models/verdicts.py  — VerdictRow "verdicts": id, alert_id (FK alerts.id), severity int (CheckConstraint "severity BETWEEN 1 AND 5", name="ck_verdicts_severity"),
  #   category str, confidence float (REAL), reasoning str, recommended_action str, escalate bool, model_primary str, model_final str, escalated_model bool,
  #   prompt_version str, input_tokens int|None, output_tokens int|None, cost_usd Decimal|None (Numeric(10,6)), latency_ms int|None, created_at (server_default=now())
  # core/models/tool_calls.py — ToolCallRow "tool_calls": id, verdict_id (FK verdicts.id), seq int, tool_name str, arguments JSONB, result JSONB, latency_ms int|None
  # core/models/eval_runs.py  — EvalRunRow "eval_runs": id, git_sha str|None, prompt_version str|None, model_config JSONB|None, started_at timestamptz|None, metrics JSONB|None
  # indexes in 0001: ix_alerts_received_at (received_at DESC), ix_alerts_src_ip ((raw->>'src_ip')), ix_verdicts_alert_id_created_at (alert_id, created_at DESC), ix_tool_calls_verdict_id_seq (verdict_id, seq)

  # alembic/env.py — URL: config sqlalchemy.url -> DATABASE_URL -> TEST_DATABASE_URL; online: make_engine(url, schema=os.environ.get("MIGRATE_SCHEMA"));
  #   async with engine.connect() as conn: await conn.run_sync(_run); context.configure(connection=..., target_metadata=Base.metadata, version_table_schema=schema); asyncio.run(...)

  # tests/conftest.py
  _DB_FIXTURE_NAMES = {"tmp_schema", "db_engine", "db_session_factory", "db_session"}
  # pytest_collection_modifyitems: if not os.environ.get("TEST_DATABASE_URL"): mark items using any of those fixtures skip(reason="TEST_DATABASE_URL unset")
  @pytest.fixture
  def tmp_schema() -> Iterator[tuple[str, str]]: ...          # SYNC. psycopg.connect(url, autocommit=True): CREATE SCHEMA sentinelbrief_test_<hex8>;
                                                              # alembic.command.upgrade(Config("alembic.ini") with sqlalchemy.url set, "head") under MIGRATE_SCHEMA=<schema>;
                                                              # yield (url, schema); DROP SCHEMA ... CASCADE
  @pytest.fixture
  async def db_engine(tmp_schema) -> AsyncIterator[AsyncEngine]: ...           # make_engine(url, schema=schema); dispose on teardown
  @pytest.fixture
  async def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]: ...
  @pytest.fixture
  async def db_session(db_session_factory) -> AsyncIterator[AsyncSession]: ...
  @pytest.fixture
  def settings() -> Settings: ...   # Settings(ingest_hmac_secret=SecretStr("test-secret"), cheap_model="fake-model", model_prices_json={"fake-model": ModelPrice(input_per_mtok=Decimal("0"), output_per_mtok=Decimal("0"))})
  ```

  CI: service `postgres:16` (`POSTGRES_PASSWORD: postgres`, `POSTGRES_DB: sentinelbrief_test`,
  `pg_isready` healthcheck); env `TEST_DATABASE_URL:
  postgresql://postgres:postgres@localhost:5432/sentinelbrief_test` (inside the runner; locally the test DB is `127.0.0.1:5434`).

## Steps (TDD)

- [ ] **Step 1: Write failing tests.** `tests/test_db.py`: `test_make_engine_normalizes_driver`
  (`engine.url.drivername == "postgresql+psycopg"`), `test_search_path_option_set`.
  `tests/test_models_schema.py` (DB): `test_alert_insert_sets_id_received_at_pending`,
  `test_duplicate_fingerprint_raises_integrity_error`,
  `test_verdict_severity_check_rejects_out_of_range`, `test_verdict_requires_existing_alert`,
  `test_migration_creates_all_four_tables` (inspector on the test schema),
  `test_indexes_present` (the four names), `test_db_fixtures_skip_without_test_database_url`
  (uses `pytester`/`monkeypatch.delenv` on a tiny inline test module → the DB test is reported as
  skipped, not passed). `tests/test_alembic_roundtrip.py`: `test_upgrade_downgrade_upgrade`,
  `test_compare_metadata_empty` (`alembic.autogenerate.compare_metadata` → `[]`).
- [ ] **Step 2: Run to see them fail** (with the export line) → Expected:
  `ModuleNotFoundError: core.db` / fixture not found.
- [ ] **Step 3: Add deps; implement `core/db.py`, the row modules, `Base`.**
- [ ] **Step 4: `alembic init`-equivalent files hand-written per Interfaces; write
  `0001_initial_schema.py` by hand (autogenerate as a draft only); `revision = "0001"`.**
- [ ] **Step 5: Implement the conftest fixtures; `database_url` on `Settings`; `.env.example`
  already documents it (verify).**
- [ ] **Step 6: Run the tests with the export line → pass, 0 skipped. Unset it → the DB tests
  report `skipped`.**
- [ ] **Step 7: CI Postgres service; full gates → commit:**
  `feat(core): async DB layer, PRD §5 rows, Alembic 0001, throwaway-schema fixtures (m2 task-01)`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_db.py tests/test_models_schema.py tests/test_alembic_roundtrip.py   # 11 passed, 0 skipped
env -u TEST_DATABASE_URL uv run pytest -q tests/test_models_schema.py                          # N skipped (never "passed")
DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head && uv run alembic downgrade base   # both succeed
uv run lint-imports                                                                            # 5 kept (core.models still a leaf)
```

## Acceptance

- Every §5 table, constraint and index exists after `upgrade head`; `compare_metadata` is empty.
- DB tests run in isolated schemas and skip (recorded) without the env var.
- `core.models` imports nothing from the project.
