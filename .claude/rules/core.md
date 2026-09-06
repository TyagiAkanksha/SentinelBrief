---
paths: core/**, alembic/**
---

# Rules for `core/` and `alembic/` (shared models, config, DB, migrations)

- `core/models/` is a **pure leaf**: it imports stdlib and third-party packages only (import-linter
  contract 1). ORM classes carry the `Row` suffix (`AlertRow`, `VerdictRow`, `ToolCallRow`,
  `EvalRunRow`); Pydantic DTOs live in `core/schemas/` and keep the PRD names (`Verdict`,
  `SessionAlert`).
- SQLAlchemy **2.0 style only**: `DeclarativeBase`, `Mapped[T]`, `mapped_column()`. Async engine
  via `core/db.py::make_engine` (psycopg 3, `postgresql+psycopg://`); never call
  `create_async_engine` anywhere else.
- **Alembic is the only DDL path.** No `create_all()`. Table and column shapes come verbatim from
  PRD §5; the indexes named there ship in the same migration as their tables. **Never edit an
  applied migration** — write the next one. Autogenerate output is hand-reviewed.
- `alembic/env.py` resolves the URL ini → `DATABASE_URL` → `TEST_DATABASE_URL`, honors
  `MIGRATE_SCHEMA` (throwaway test schemas), and runs online migrations through the async engine
  with `run_sync`.
- `core/config.py::Settings` is the single config surface. Zero-env constructible; secrets are
  `SecretStr`; complex values (`MODEL_PRICES_JSON`) are typed fields, not hand-parsed strings.
  **Adding a field means adding its documented line to `.env.example` in the same commit** —
  `tests/test_env_example_roster.py` enforces it.
- Never hardcode a model id, price, threshold, cap, or secret anywhere under `core/`.
- `core/llm.py` is an interface: Protocol, dataclasses, `parse_structured`, `compute_cost_usd`.
  It never imports an LLM SDK. `core/errors.py` never imports `core/llm.py` (the
  `StructuredOutputError` carries plain values).
- `core/services/` functions are async and session-first; they `flush()` and never `commit()`.
- `core/signing.py` is shared by the API, the shipper, and `scripts/`; keep it dependency-free
  (stdlib `hmac`/`hashlib` only) and fail-closed on an empty secret.
