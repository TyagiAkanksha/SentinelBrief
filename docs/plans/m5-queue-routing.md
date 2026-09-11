# m5-queue-routing — Queue split + routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M5 — **authoritative.** Primary sections: §3 (invariants: 202 in <100 ms,
all LLM work in the worker), §6.1 step 3 (enqueue), §6.2 (3 attempts with backoff, poison →
`failed`, `verdict.created` publish), §6.4 (two-tier routing, thresholds in config, both models
recorded), §8 (`/healthz` DB + Redis), §4 (ARQ, Redis 7).
**Conventions:** `CONVENTIONS.md` §2, §3, §5, §11 · `.claude/rules/{api,worker,infra}.md`.

**Goal:** the ingest route only inserts and enqueues (`202` in <100 ms); a separate `worker`
container runs ARQ and executes the triage job; retries with backoff and a poison path that never
wedges the queue; two-tier routing (cheap → strong when severity ≥ `ESCALATE_SEVERITY_GTE` or
confidence < `ESCALATE_CONFIDENCE_LT`) with `model_primary`, `model_final`, `escalated_model`
recorded; idempotent job execution (a job re-run after a mid-job kill produces no duplicate
verdict); `/healthz` pings Redis; the M2 `api.main` contract exception is removed.

**Architecture:** `core/queue.py` is the one module that knows ARQ's job name (`triage_alert`),
queue (`sentinelbrief:triage`) and job id (`triage:<alert_id>`); `api/` enqueues through it and
never imports `worker`. `worker/main.py` holds `WorkerSettings` (ARQ) whose `startup` builds the
engine, the `httpx` client, the LLM client and the pipeline once and whose `shutdown` closes them;
`worker/jobs.py::triage_alert_job` wraps `TriagePipeline.triage_attempt` and delegates the
retry-or-fail decision to the pure `worker/retry.py`. Idempotency: the attempt loads the alert
with `SELECT … FOR UPDATE`, holds the lock through the LLM/tool loop and the single verdict
transaction, and returns `skipped` when the row is no longer `pending` (M8's retriage flips it
back deliberately). Routing lives in `worker/routing.py` as a pure decision plus one tool-less
strong-model call over the cheap pass's conversation. `verdict.created` is published on Redis
pub/sub after the commit (M8's SSE consumes it). `core.cache.RedisTTLCache` backs the worker's
AbuseIPDB cache behind the M3 `TTLCache` Protocol (bounded by distinct IPs × the 24 h TTL); the
api's list/stats cache stays the bounded in-process `InMemoryTTLCache` — a public route must never
grow the Redis that also holds the queue (task-05 review I2, ruling R14); `api/factory.py` gains
`enqueue` and `redis` seams.

**Tech Stack:** M4 stack + `arq` 0.28 · `redis` 5 (asyncio) · compose `redis:7-alpine` and
`worker` services · a dedicated test Redis (`TEST_REDIS_URL`).

## Global Constraints

M0–M4 Global Constraints apply verbatim (branch `feat/m5-queue-routing`). Additionally:

- **Enqueue only after the insert is committed (M2 final review M5-a).** `SessionDep` commits after
  the handler returns, so an `enqueue` inside the handler would run before the row is visible to
  the worker. **Ruling (briefing):** task-01 keeps the explicit route commit before `enqueue`
  (the M2 carve-out comment is reworded, not deleted) and pins it with a fake enqueue that
  observes the row from a fresh session. `TriageFn` becomes `EnqueueFn =
  Callable[[uuid.UUID], Awaitable[None]]` and the route answers `pending`; a duplicate POST of a
  still-`pending` alert re-enqueues (idempotent at the queue by job id) so a `503
  queue_unavailable` at ingest is healed by the shipper's retry; the `api.main -> worker.*`
  `ignore_imports` lines go in the same commit.
- **Pins that move into the worker entrypoint (M5-b).** The settings→pipeline wiring pin moves
  from `tests/test_api_main.py` to `tests/test_worker_main.py` (task-01); the failure-path
  `logger.warning` gets caplog pins (task-02); `alembic/env.py` raises `ConfigError("DATABASE_URL
  is not set")` on an empty URL (M2 final review M2) alongside the shared CLI helper extraction
  (task-05).
- **Terminal state on unexpected exceptions (M2 task-04 review M7).** **Ruling:** the job catches
  `Exception` (never `BaseException`) around the whole attempt; every family except
  `NotFoundError` follows one policy — retry with backoff below the last try, `failed` on it, with
  the exception class logged (task-02; the third CONVENTIONS §4 carve-out). `pending` rests only
  when the terminal write itself cannot reach the database (documented residual). **An attempt is
  bounded INSIDE the job by `TRIAGE_ATTEMPT_TIMEOUT_S` (below `TRIAGE_JOB_TIMEOUT_S`, boot-checked)
  so a hung attempt becomes a retryable `TimeoutError` under the same policy; ARQ's `job_timeout`
  is only the backstop and, if it fired first, would record the job failed with no retry and no
  terminal write (m5 final review N-I1, ruling R15 shape (a)).**
- **Attempt count (ruling).** `TRIAGE_JOB_MAX_TRIES = 3` counts TOTAL attempts (the first run plus
  two retries) — the number PRD §6.2's cost bound multiplies by; the PRD's "retried up to 3 times"
  wording is amended to say so (v1.4, task-02). Cost if wrong: one Setting bump.
- **Strong tier (ruling).** The strong model gets the cheap pass's conversation as it stands
  (system + delimited summary + every tool call and delimited result, plus the forced-final
  instruction on the cap path), never the cheap verdict, and answers with no tools — one call plus
  the one §6.5 retry. A strong-tier failure fails the attempt (retried by task-02); there is no
  fallback to the cheap verdict, because `escalated_model=True` with `model_final=cheap` would
  misrecord the decision. PRD §6.2's bound becomes `(TOOL_LOOP_MAX_ITER + 4) × 3` and §6.4 gains
  the two sentences (v1.4, task-03). Cost if wrong: extra cheap-tier spend during a strong-tier
  outage, bounded by the retry budget.
- **Row lock, not `SKIP LOCKED` (ruling).** The attempt's `FOR UPDATE` is blocking: an overlapping
  duplicate run waits for the truth and then skips; if the holder dies without committing, the
  waiter sees `pending` and does the work. The lock is held across the LLM call on purpose
  (task-04). Cost if wrong: a blocked worker slot for the duration of one attempt.
- **Remove the `api.main → worker.*` `ignore_imports` exception** in task-01; contract 3 is
  absolute from here on. `api/` enqueues by job name through `core.queue` and never imports
  `worker`; the contract-verification ritual is run and pasted.
- Ingest latency is measured in a test with a fake queue (median of 5 < 100 ms; the docstring
  says what it pins — M4 plan-defect rule 14) and in the acceptance burst (50 signed POSTs, all
  `202`, p95 < 100 ms, timings pasted).
- Retry count, backoff, routing thresholds, timeouts and the quota back-off are settings with
  `.env.example` lines; the routing and retry decisions are pure functions with exhaustive unit
  tests; defaults are read from `Settings()` except in the deliberately tautological defaults
  tests (R17: literal + comment).
- A job must be safe to run twice: no duplicate verdict rows, no double token spend recorded
  (task-04 proves five re-run shapes, each stating its ordering assumption — rule 9).
- Tests for ARQ use a real Redis: `TEST_REDIS_URL` joins the env-export line — **`export
  TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
  TEST_REDIS_URL=redis://127.0.0.1:6380/0`** — pointing at the DEDICATED container
  `sentinelbrief-test-redis` (the fixtures `flushdb`; never the dev compose Redis on 6379); a
  missing value skips by fixture name, recorded not passed; CI runs a `redis:7` service and fails
  on any skip.
- Every "unchanged / byte-for-byte" claim has a mechanical definition in its brief (rule 2);
  every exception boundary states its family, log shape and caller-visible result (rule 1); Redis
  keys, job ids and channel names are spelled out in the Interfaces blocks (rule 6); leak pins use
  distinct inputs per branch (rule 5); fix-wave briefs quote the reviewer's fix shape verbatim
  (M4 re-review N1).
- **STRONG_MODEL (owner input):** the local `.env` carries `STRONG_MODEL=gpt-5.4-2026-03-05` once
  its `MODEL_PRICES_JSON` entry is added by the owner; tests never depend on it. Until then the
  live escalation clause is "owner input pending" in the reports and the controller runs it at
  the gate.

## Tasks (briefs written at the M4 gate, 2026-09-09)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Redis + ARQ: `core/queue.py`, `enqueue_triage` from ingest (`202 pending`), `worker/jobs.py` + `worker/main.py`, compose `redis` + `worker` services, CI/test Redis fixtures, contract exception removed, `<100 ms` test, `httpx` ownership (`aclose`), evals registry once | `m5-queue-routing/task-01-arq-worker-enqueue.md` | M4 tag |
| 2 | Retry with exponential backoff (`worker/retry.py`), terminal `failed` on the last try for every exception family, `triage_attempt`, `verdict.created` publish after commit, PRD v1.4 wording, CONVENTIONS §4 third carve-out | `m5-queue-routing/task-02-retry-poison-publish.md` | task-01 |
| 3 | Two-tier routing: `worker/routing.py::should_escalate`, strong-model second pass over the same conversation, routing fields persisted, `--strong-model` on the CLIs, `escalation_rate` in evals, PRD v1.4 bound/§6.4, loop-test helper lift | `m5-queue-routing/task-03-two-tier-routing.md` | task-02 |
| 4 | Idempotency: `get_alert_for_update`, skip when not `pending`, cancel/crash/overlap/double-run proofs with a real `Worker`, the live kill procedure | `m5-queue-routing/task-04-idempotency.md` | task-02, task-03 |
| 5 | `/healthz` Redis ping; `RedisTTLCache` for the api response cache and the reputation cache; AbuseIPDB quota back-off; cache-hit-no-DB pin; `core/cli.py`; `alembic/env.py` URL guard; `seed_dev` triples | `m5-queue-routing/task-05-healthz-redis-caches.md` | task-01 |

Order (dispatched sequentially): 1 → 2 → 3 → 4 → 5. Rationale: the queue split is the
structural change everything else sits on; the retry policy defines `triage_attempt`, which
routing (persisted fields) and idempotency (the lock) both modify, so they follow it in that
order; task-05 depends only on task-01 but is dispatched last because it refactors the same CLIs
task-03 extends and swaps the caches both entrypoints wire.

## Acceptance walk (PRD §12 M5)

| Clause | Demonstrated by |
|---|---|
| ARQ worker as separate container; ingest returns in <100 ms; Redis | task-01 compose + latency test; burst of 50 signed POSTs (distinct `session_id`s minted from the fixtures + golden set) → all `202 pending`, p95 < 100 ms, timings pasted |
| Two-tier routing live with config thresholds | task-03 tests; live `fixtures/alerts/alert5.json` through the stack shows `escalated_model=true`, `model_final=STRONG_MODEL`, `alert1.json` shows `false` (needs the owner's `STRONG_MODEL` + price) |
| Retry/poison handling | task-02: the poison test (three attempts, then `failed`, the queue keeps draining) and the transient-retry test with a real `Worker`; live: `docker compose … logs worker` after the burst shows no `pending` survivor |
| Burst of 50 POSTs → all triaged eventually | the acceptance script polls `alerts.status` until every one of the 50 is `triaged`/`failed`; counts pasted |
| Kill the worker mid-job → job re-runs, no duplicate verdicts | task-04 tests + the live `docker kill` procedure in its brief (lease = `TRIAGE_JOB_TIMEOUT_S + 10` s; the walk sets 60 s); rehearsed 2026-09-10 on the dev stack: kill mid-LLM → ARQ re-ran at `job_try=2` 70.63 s after the enqueue (≈ the 70 s lease), `←` 5.27 s later — ≈ 75.9 s kill-to-`triaged`; `count(*)` per alert = 1 pasted |
| `/healthz` reports Redis | task-05: `stop redis` → 503 `redis: "error"` and the api container turns unhealthy; `start redis` → 200 |

## Status

done — briefs written 2026-09-09 (`m5-queue-routing/task-01` … `task-05`); five tasks approved,
whole-branch review TAG-READY after one fix wave, acceptance walk pasted in the ledger; PR #6
merged into `main` at `50b108b`, tag `m5` (2026-09-11). Git history and the ledger
(`.superpowers/sdd/m5-queue-routing/progress.md`) are authoritative.
