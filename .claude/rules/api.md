---
paths: api/**
---

# Rules for `api/` (FastAPI request path)

- **No LLM here.** `api/` never imports `worker` or `core.llm` (import-linter contract 3; PRD
  §10.1). The only exception is `api/main.py` wiring the inline-triage seam during M2, declared
  via `ignore_imports` and removed at M5. Route modules only ever see a `TriageFn` callable.
- **Routes contain no `try/except`.** Raise the typed errors from `core/errors.py`; the envelope
  is produced once by `api/errors.py::register_error_handlers`. The single carve-out is
  `GET /healthz` answering `503 degraded` on DB/Redis failure.
- Every route declares a stable, unique `operation_id`. Changing a route or DTO regenerates
  `api/openapi.json` (`scripts/export_openapi.py`) and, from M3, `web/` codegen — in the same
  commit.
- All routes live under `/api/v1`, applied once in `api/factory.py`; `/healthz` is the only root
  path.
- `create_app()` must keep working with no env vars and no database (DB-less tests, OpenAPI
  export). Read request-scoped things from `app.state` through `api/deps.py`, never from module
  globals or `os.environ`.
- Ingest (`POST /api/v1/alerts`): verify the HMAC signature over the **raw body before parsing
  JSON** (401 precedes 422); insert with `ON CONFLICT DO NOTHING`; duplicates return 200 and
  never re-trigger triage; from M5 the route only enqueues and must answer in under 100 ms.
- Public GET paths never compute: no LLM, no tool call, no retriage. `retriage` is admin-token
  gated and globally capped (M8).
- Validation errors are enveloped as 422 with location and message only — never echo the input.
- Services are called session-first; the `get_session` dependency owns commit/rollback. The one
  M2-only exception (committing the insert before awaiting inline triage) is documented in the
  route and disappears at M5.
