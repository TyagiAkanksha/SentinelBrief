# m2-service-persistence — Service + persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M2 — **authoritative.** Primary sections: §5 (schema, verbatim), §6.1
(ingest, signature-before-parse, fingerprint, dedup), §6.2 (one transaction per verdict write),
§8 (`/healthz`, envelope, `operation_id`, OpenAPI baseline), §11 (Dockerfile/compose shape).
**Conventions:** `CONVENTIONS.md` §2–§8, §10–§11 · `.claude/rules/{api,core,tests,infra}.md`.

**Goal:** FastAPI ingest with HMAC + fingerprint dedup; Postgres via Alembic migration 0001;
triage runs **inline** in the request (no queue yet — M5 replaces it with ARQ); alerts + verdicts
persisted with the verdict, its (empty) tool trace and the status update in one transaction;
`docker compose up` (api + postgres) works from a clean clone.

**Architecture:** `core/db.py` builds an async engine on psycopg 3; ORM rows (`AlertRow`,
`VerdictRow`, `ToolCallRow`, `EvalRunRow`) are a pure leaf; Alembic `0001_initial_schema` is the
only DDL; tests get a throwaway schema per test through a sync `tmp_schema` fixture.
`api/factory.py::create_app(session_factory, settings, triage)` is DB-less constructible and
exposes exactly one seam to the worker — a `TriageFn` callable — so the route layer never imports
`worker`; `api/main.py` is the single wiring point and carries the M2-only `ignore_imports`
exception for inline triage. `core/signing.py` (stdlib HMAC) is shared by the API, the future
shipper, and `scripts/post_alert.py`. `worker/store.py::persist_verdict` is the one-transaction
write; `TriagePipeline.triage_alert(session, alert_id)` is the unit ARQ wraps at M5.

**Tech Stack:** M0 stack + `sqlalchemy[asyncio]>=2` · `psycopg[binary]>=3` · `alembic` · `fastapi`
· `uvicorn` · Docker (`postgres:16`, multi-stage uv image).

## Global Constraints

M0 and M1 Global Constraints apply verbatim (branch is `feat/m2-service-persistence`).
Additionally:

- **The env-export line is part of every dispatch, verbatim:**
  `export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief_test`
  (a dedicated database, never the dev one). pytest evidence without it is invalid; `uv run
  pytest -q` must report **0 skipped** with it set.
- Schema shapes and index names come verbatim from PRD §5. **Never edit migration 0001 after it
  is committed** — a later change is `0002`.
- Services never `commit()`; the `get_session` dependency and the worker job own transactions.
  The one documented M2 exception: the ingest route commits the insert **before** awaiting
  inline triage so the alert row survives a triage failure. It disappears at M5.
- `api.*` never imports `worker` or `core.llm` except `api.main` (contract 3 `ignore_imports`,
  commented "M2 inline triage; remove at M5").
- Signature is verified over the raw body **before** JSON parsing: an unsigned invalid body is
  `401`, not `422`.
- `api/openapi.json` is committed from task-02 on and regenerated in the same commit as any
  route/DTO change; CI diffs it.
- Migrations never run at container start; the compose file's `api` command is uvicorn only.

## Tasks

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Async DB layer, ORM rows, Alembic 0001, throwaway-schema test fixtures, CI Postgres service | `m2-service-persistence/task-01-db-models-alembic-fixtures.md` | M1 tag |
| 2 | `create_app` factory, deps, error envelope, `/healthz`, `api/main.py`, OpenAPI baseline, env-roster test | `m2-service-persistence/task-02-app-factory-healthz-baseline.md` | task-01 |
| 3 | HMAC signing, alert service (insert with dedup), `POST /api/v1/alerts` | `m2-service-persistence/task-03-signing-ingest-dedup.md` | task-02 |
| 4 | `persist_verdict` (one transaction), `TriagePipeline.triage_alert`, inline wiring in `api/main.py` | `m2-service-persistence/task-04-store-inline-triage.md` | task-03 |
| 5 | `infra/Dockerfile.api`, `infra/docker-compose.yml`, `scripts/post_alert.py`, README quickstart, M2 acceptance + tag | `m2-service-persistence/task-05-dockerfile-compose-acceptance.md` | task-04 |

Order: 1 → 2 → 3 → 4 → 5. Rationale: models and fixtures first (everything DB-touching imports
them); the factory pins the injection seams (`session_factory`, `settings`, `triage`) so task-03
can test ingest with a fake triage callable and task-04 can swap in the real pipeline without
changing the route; the image and compose come last because the acceptance walk runs through
them.

## Acceptance walk (PRD §12 M2)

| Clause | Demonstrated by |
|---|---|
| FastAPI ingest with HMAC + fingerprint dedup | task-03 `test_signed_post_202_and_inserts_row`, `test_duplicate_post_200_same_id_no_new_row`, `test_signature_checked_before_body_validation` |
| Postgres via Alembic migration 0001 | task-01 `test_migration_creates_all_four_tables`, `test_compare_metadata_empty` |
| Triage runs inline; alerts + verdicts persisted | task-04 `test_signed_post_creates_alert_and_verdict_rows`, `test_persist_verdict_is_all_or_nothing` |
| `docker compose up` (api+postgres) → signed POST → row in both tables | task-05 walk: `up -d --build` → migrate → `scripts/post_alert.py fixtures/alerts/alert4.json` → `202` → `psql` counts pasted |
| Duplicate POST → 200, no new row | task-05 walk: second `post_alert.py` → `200`, counts unchanged |
| Unsigned → 401 | task-05 walk: bare `curl -X POST` → `401` envelope |
| (v1.1) image builds; `docker compose config` validates | task-05 `test_compose_config_validates`; build log pasted |

## Status

planned — snapshot only; git history and the ledger are authoritative.
