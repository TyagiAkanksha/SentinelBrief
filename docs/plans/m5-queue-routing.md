# m5-queue-routing — Queue split + routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M5 — **authoritative.** Primary sections: §3 (invariants: 202 in <100 ms,
all LLM work in the worker), §6.1 step 3 (enqueue), §6.2 (3 retries with backoff, poison →
`failed`, `verdict.created` publish), §6.4 (two-tier routing, thresholds in config, both models
recorded), §8 (`/healthz` DB + Redis), §4 (ARQ, Redis 7).
**Conventions:** `CONVENTIONS.md` §2, §3, §5, §11 · `.claude/rules/{api,worker,infra}.md`.

**Goal:** the ingest route only inserts and enqueues (`202` in <100 ms); a separate `worker`
container runs ARQ and executes `triage_alert`; retries with backoff and a poison path that never
wedges the queue; two-tier routing (cheap → strong when severity ≥ `ESCALATE_SEVERITY_GTE` or
confidence < `ESCALATE_CONFIDENCE_LT`) with `model_primary`, `model_final`, `escalated_model`
recorded; idempotent job execution (a job re-run after a mid-job kill produces no duplicate
verdict); `/healthz` pings Redis; the M2 `api.main` contract exception is removed.

**Architecture:** `worker/main.py` holds `WorkerSettings` (ARQ) with `on_startup` building the
engine, LLM client and pipeline once; the job function wraps `TriagePipeline.triage_alert`.
Idempotency: the job re-checks `alerts.status` under `SELECT … FOR UPDATE` and returns early if a
verdict already exists for this run (retriage at M8 creates a new row deliberately). Routing
lives in `worker/routing.py` as a pure decision function plus a second `TriagePipeline.run` on
the strong model with the same messages and tool trace. Redis pub/sub `verdict.created` is
published after commit (consumed by SSE at M8). The AbuseIPDB cache and rate-limit counters move
to Redis behind the Protocols introduced in M3/M4. `api/factory.py` gains a `redis` seam.

**Tech Stack:** M4 stack + `arq` · `redis` (asyncio) · compose `redis:7` and `worker` services.

## Global Constraints

M0–M4 Global Constraints apply verbatim (branch `feat/m5-queue-routing`). Additionally:

- **Enqueue only after the insert is committed (M2 final review M5-a).** `SessionDep` commits after the handler returns, so an `enqueue` inside the handler would run before the row is visible to the worker. Task-01 keeps the explicit route commit before `enqueue` (the M2 carve-out comment is reworded, not deleted) or enqueues from a post-commit hook — the brief says which and pins it. `TriageFn` becomes `EnqueueFn = Callable[[uuid.UUID], Awaitable[None]]` and the route answers `pending`; the `api.main -> worker.*` `ignore_imports` lines go in the same commit.
- **Pins that move into the worker entrypoint (M5-b).** The settings→pipeline wiring pin moves from `tests/test_api_main.py` to the `worker/main.py` tests; the failure-path `logger.warning` gets a caplog pin; `alembic/env.py` raises `ConfigError("DATABASE_URL is not set")` on an empty URL (M2 final review M2) alongside the shared CLI helper extraction.
- **Terminal state on unexpected exceptions (M2 task-04 review M7).** `triage_alert` today catches only `VerdictValidationError | LLMCallError` and leaves the alert `pending` on anything else. The ARQ job brief decides the terminal state after the last retry (`failed`, with the exception class logged) and pins it with a test; `pending` must never be a resting state once the queue exists.
- **Remove the `api.main → worker.*` `ignore_imports` exception** in task-01; contract 3 is
  absolute from here on. `api/` enqueues by job name through ARQ and never imports `worker`.
- Ingest latency is measured in a test with a fake queue (<100 ms budget asserted with margin)
  and in the acceptance burst (50 signed POSTs, all `202`, p95 < 100 ms).
- Retry count, backoff, and routing thresholds are settings; the routing decision is a pure
  function with exhaustive unit tests.
- A job must be safe to run twice: no duplicate verdict rows, no double token spend recorded.
- Tests for ARQ use a real Redis (compose or service container) — `TEST_REDIS_URL` joins the
  env-export line; a missing value skips by fixture name, recorded not passed.

## Tasks (briefs written at the M4 gate)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Redis + ARQ: `worker/main.py`, `enqueue_triage` from ingest, compose `redis` + `worker` services, contract exception removed, `<100 ms` test | `m5-queue-routing/task-01-arq-worker-enqueue.md` | M4 tag |
| 2 | Retry with backoff, poison handling → `failed`, `verdict.created` publish after commit | `m5-queue-routing/task-02-retry-poison-publish.md` | task-01 |
| 3 | Two-tier routing: decision function, strong-model second pass, verdict routing fields, `evals.run` reports escalation rate | `m5-queue-routing/task-03-two-tier-routing.md` | task-01 |
| 4 | Idempotency: `FOR UPDATE` status check, kill-mid-job test, no duplicate verdicts | `m5-queue-routing/task-04-idempotency.md` | task-02 |
| 5 | `/healthz` Redis ping; AbuseIPDB cache and future counters on Redis behind the Protocols | `m5-queue-routing/task-05-healthz-redis-caches.md` | task-01 |

Order: 1 → (2, 3, 5 in parallel) → 4. Rationale: the queue split is the structural change
everything else sits on; routing and retries are independent behaviors of the job; idempotency is
proven last against the finished job.

## Acceptance walk (PRD §12 M5)

| Clause | Demonstrated by |
|---|---|
| ARQ worker as separate container; ingest returns in <100 ms; Redis | task-01 compose + latency test; burst of 50 signed POSTs → all `202`, timings pasted |
| Two-tier routing live with config thresholds | task-03 tests; a live sev-5 fixture shows `escalated_model=true`, `model_final=STRONG_MODEL` |
| Retry/poison handling | task-02: a fixture that always fails validation ends `failed` after the configured retries; queue keeps draining |
| Burst of 50 POSTs → all triaged eventually | acceptance script polls `alerts.status` until all `triaged`/`failed`; output pasted |
| Kill the worker mid-job → job re-runs, no duplicate verdicts | task-04 test + live `docker kill` during a job; verdict count per alert = 1 |

## Status

planned — briefs pending (written at the M4 gate).
