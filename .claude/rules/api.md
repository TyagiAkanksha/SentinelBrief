---
paths: api/**
---

# Rules for `api/` (FastAPI request path)

- **No LLM here.** `api/` never imports `worker` or `core.llm` (import-linter contract 3; PRD
  §10.1). There are no exceptions as of m5 task-01. Route modules only ever see an `EnqueueFn`
  callable.
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
- Ingest (`POST /api/v1/alerts`): the in-app body cap (`require_content_length`, 411/413) runs
  BEFORE the signature check — it reads only `Content-Length`, never a body byte (m6 task-02).
  Then verify the HMAC signature over the **raw body before parsing JSON** (401 precedes 422);
  insert with `ON CONFLICT DO NOTHING`; duplicates return 200; a
  duplicate of a triaged/failed alert never re-triggers triage, while a still-`pending` duplicate
  is re-enqueued (idempotent at the queue by job id — spine M5-a); from M5 the route only
  enqueues and must answer in under 100 ms.
- Public GET paths never compute: no LLM, no tool call, no retriage. `retriage` is admin-token
  gated and globally capped (M8).
- Validation errors are enveloped as 422 with location and message only — never echo the input.
- Services are called session-first; the `get_session` dependency owns commit/rollback. Routes
  take `session: SessionDep` (scope="function"), never bare `Depends(get_session)`, so the commit
  completes before the response is sent. The route commits the insert **before** awaiting
  `enqueue` (spine M5-a: the row must be durable before a worker can pick the job up); the comment
  in `api/routes/alerts.py` says so.
