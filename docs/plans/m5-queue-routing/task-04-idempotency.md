---
id: task-04
milestone: m5-queue-routing
depends_on: [task-02, task-03]
status: planned
spec: PRD.md §6.1 (idempotency), §6.2 (verdict + trace + status in ONE transaction), §5 (`verdicts`: one row per triage run — retriage creates a new row deliberately, M8), §12 M5 ("kill the worker mid-job → job re-runs, no duplicate verdicts"); CONVENTIONS.md §3 (the job owns its commit; `flush()`-only services), §6, §10 (real DB, real Redis; mock only external seams); `.claude/rules/{worker,tests}.md`; M5 spine Global Constraints ("A job must be safe to run twice: no duplicate verdict rows, no double token spend recorded"; the kill-mid-job test states its ordering assumptions — M4 plan-defect rule 9)
---

# task-04 — Idempotent job execution: `get_alert_for_update` (row lock for the whole attempt), skip when the alert is no longer `pending`, cancellation/crash re-run proofs, the kill-mid-job procedure for the acceptance walk

## Goal

`TriagePipeline.triage_attempt` loads the alert with `SELECT … FOR UPDATE`
(`core.services.alerts.get_alert_for_update`) and holds that row lock for the whole attempt —
through the LLM/tool loop, `persist_verdict`, and the commit or rollback. If the locked row's
status is not `pending` (it was triaged or failed by an earlier run that committed), the attempt
releases the lock and returns `AttemptResult(status="skipped")` **before any LLM call**, so a job
that runs twice — ARQ re-running it after a worker crash, a graceful restart, or an operator
re-enqueue — can never write a second verdict row or spend a second set of tokens. Two runs of the
same alert that overlap serialise on the lock: the second waits (a blocking `FOR UPDATE`, not
`SKIP LOCKED` — if the first dies without committing, the lock is released with its rollback and
the waiter then sees `pending` and does the work itself), then skips. Nothing is written before
the final `persist_verdict`, so a run killed mid-LLM leaves no trace at all: the connection drop
rolls the open transaction back. The tests prove three re-run shapes with a real `Worker`: (1)
cancelled mid-LLM and re-run immediately (the SIGTERM path ARQ handles itself), (2) crashed
mid-LLM with ARQ's in-progress lease still held and then expired (the SIGKILL path — the lease is
`TRIAGE_JOB_TIMEOUT_S + 10` s, emulated by deleting the key), (3) crashed after the commit but
before ARQ finished the job (re-run → skipped). Plus the overlap test and the direct
double-invocation test. The live `docker kill` procedure and its expected timings are written
down for the milestone's acceptance walk. M8's retriage is told how to opt out of the skip.

## Context (read ONLY these)

- `PRD.md` §5 (`verdicts` comment: retriage creates a new row), §6.1, §6.2, §12 M5.
- `docs/plans/m5-queue-routing.md` — Global Constraints (safe to run twice; ordering assumptions
  stated; the acceptance walk row for the kill).
- `CONVENTIONS.md` §3, §6, §10; `.claude/rules/{worker,tests}.md`.
- Task-02 outputs: `triage_attempt` / `AttemptResult` (`worker/triage.py`), `triage_alert_job`
  (`worker/jobs.py`; its `"skipped"` result is already in `JobResult`), `tests/test_worker_job.py`
  and `tests/test_worker_job_retry.py` (the burst-worker pattern).
- Task-03: `triage_attempt`'s `persist_verdict` call carries the routing fields (unchanged here).
- Code you build on: `core/services/alerts.py` (`get_alert` stays for every other caller),
  `worker/tools/alert_history.py` (its SAVEPOINT runs inside the locked transaction — fine: it
  only SELECTs), `tests/helpers.py::seed_alert`.
- ARQ 0.28 facts: a worker cancelled while a job runs (task cancellation / SIGTERM) logs
  "cancelled, will be run again", deletes `arq:in-progress:<job_id>` and leaves the job in the
  queue set, so the next worker runs it at once; a worker killed with SIGKILL leaves
  `arq:in-progress:<job_id>` until its lease (`job_timeout + 10` s) expires, and the next worker
  skips the job ("already running elsewhere") until then; `job_try` increments on every start;
  `worker.job_tasks` maps running job ids to tasks; `await worker.close()` after `main()` closes
  the pool. `Job(job_id, redis, _queue_name=…).status()` reports `queued` / `in_progress` /
  `complete` / `not_found`.

## Files

- Create (test-author): `tests/test_job_idempotency.py`
- Modify (test-author, re-pinned): `tests/test_triage_alert.py` (+ the lock/skip pins),
  `tests/test_alert_service.py` (+ `get_alert_for_update`)
- Modify: `core/services/alerts.py`, `worker/triage.py`, `worker/jobs.py` (the docstring gains
  the `"skipped"` result and the M8 note; AND the task-02 review's M7 lift: the `except Exception`
  body of `triage_alert_job` moves into `async def _handle_attempt_failure(exc: Exception, *,
  ctx-derived args) -> JobResult` so the happy path stays about ten lines — behaviour-preserving,
  every task-02 pin stays green unchanged, the reviewer mutation-tests it), `docs/plans/m5-queue-routing.md` (Acceptance walk: the
  kill procedure row gets its timings — see Step 6), `README.md` (one sentence in Quickstart:
  a worker restart re-runs interrupted jobs without duplicating verdicts)

## Interfaces

- **Consumes:** `AlertRow`, `NotFoundError`; `triage_attempt`, `AttemptResult`,
  `triage_alert_job`; `Worker`, `func`, `Job`; `arq` key prefixes (`arq:in-progress:`);
  `FakeLLMClient` (its `complete_structured` awaits an injected `asyncio.Event` in the blocking
  tests — a fake of the external LLM seam, not of our code).
- **Produces (M8 retriage relies on — produce exactly):**

  ```python
  # core/services/alerts.py
  async def get_alert_for_update(session: AsyncSession, alert_id: uuid.UUID) -> AlertRow: ...
      # (await session.execute(select(AlertRow).where(AlertRow.id == alert_id).with_for_update())).scalar_one_or_none()
      # None -> NotFoundError(f"alert {alert_id} not found"). Blocks while another transaction holds the row lock; the lock lives until the
      # session's commit/rollback. The row comes back clean in the identity map: nothing is dirty before the tool loop (M4 task-05 M7).

  # worker/triage.py — triage_attempt after this task
  async def triage_attempt(self, session, alert_id) -> AttemptResult:
      # row = await get_alert_for_update(session, alert_id)            # NotFoundError propagates
      # if row.status != "pending":
      #     await session.rollback()                                    # release the lock; nothing was written
      #     logger.info("triage attempt skipped alert_id=%s status=%s", alert_id, row.status)
      #     return AttemptResult(status="skipped", verdict_id=None, outcome=None)
      # ... unchanged: run -> persist_verdict -> commit; BaseException -> rollback -> raise
      # The lock is held across the LLM call(s) on purpose: a concurrent duplicate run must wait for the truth, not race it.
      # M8 retriage: PRD §5 wants a NEW verdict row per retriage; the admin route must `set_alert_status(..., "pending")` + commit
      # before enqueueing under a fresh job id (`triage:<alert_id>:retriage:<n>` — M8 defines it) — a `triaged` row is otherwise skipped by design.

  # worker/jobs.py — unchanged code; the docstring documents: "skipped" is returned when the alert was already triaged/failed when the
  # attempt looked (a re-run after a crash that had already committed, or an overlapping run) and publishes nothing.
  # triage_alert (the M2/seed contract) maps "skipped" -> "triaged" (the alert IS triaged) — pinned.
  ```

  **Live procedure (the acceptance walk; the controller runs it, the report records it):**
  1. Dev `.env` sets `TRIAGE_JOB_TIMEOUT_S=60` for the walk (lease = 70 s).
  2. POST a fresh copy of `fixtures/alerts/alert4.json` (new `session_id`) → `202 pending`; within
     ~1 s `docker compose -f infra/docker-compose.yml logs --since 5s worker` shows the job start.
  3. `docker kill sentinelbrief-worker-1` while the job is in its LLM call (the log line
     "worker ready" precedes; the job's `→ triage:<id>:triage_alert` line has appeared, no `←`
     line yet).
  4. `docker compose -f infra/docker-compose.yml up -d worker`; the job re-runs once the lease
     expires: `≤ 70 s + WORKER poll` after the kill, `logs worker` shows a second `→ triage:<id>`
     with `job_try=2`, then `←`.
  5. `psql -c "select count(*) from verdicts where alert_id='<id>'"` → `1`; `alerts.status` →
     `triaged`. Repeat once with the kill placed right AFTER the `←` line (a completed job
     whose container dies before ARQ's `finish_job` — ARQ then re-runs the job after the lease):
     `logs worker` shows a second `→ triage:<id>` line ending in `skipped`; the count stays
     `1`. Finally POST the same body again → `200 triaged`, no enqueue. All three counts pasted.

## Interfaces → test table

All tests here use `db_session_factory` (throwaway schema) and `arq_redis` (flushed). Blocking
fakes: `BlockingFakeLLMClient(FakeLLMClient)` defined in the test module — its
`complete_structured` sets `started` and awaits `release` before delegating to the parent (an
LLM double; the only thing faked is the vendor). Ordering assumptions are stated per test
(rule 9); every count is asserted after everything has finished.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| lock + NotFound | `tests/test_alert_service.py::test_get_alert_for_update_returns_the_row_or_raises` | existing alert → `AlertRow` with the id; `uuid4()` → `NotFoundError` |
| lock blocks a second reader | `tests/test_alert_service.py::test_get_alert_for_update_blocks_until_the_holder_commits` | session A locks; session B's `get_alert_for_update` (a task) is still pending after `await asyncio.sleep(0.3)`; A commits; B completes within 2 s. Assumption stated: two pooled connections on `db_engine` (pool size ≥ 2 — SQLAlchemy's default 5) |
| skip when triaged | `tests/test_triage_alert.py::test_triage_attempt_skips_an_already_triaged_alert_without_an_llm_call` | seed with a verdict (status `triaged`) → `AttemptResult(status="skipped", verdict_id=None, outcome=None)`; `len(fake.calls) == 0`; verdict count still 1; a `failed` alert → skipped too; `triage_alert` on the same alert → `"triaged"` (the mapping pinned) |
| pending runs | `tests/test_triage_alert.py::test_triage_attempt_runs_a_pending_alert` | (already covered by task-02's commit pin — keep; add the assertion that the pending row's lock is released after commit: a follow-up `get_alert_for_update` in a fresh session returns within 2 s) |
| direct double invocation | `tests/test_job_idempotency.py::test_running_the_job_twice_writes_one_verdict_and_spends_once` | `ctx` with `job_try=1`, `triage_alert_job(ctx, str(aid))` → `"triaged"`; again → `"skipped"`; 1 verdict; `len(llm.calls) == 1`; the verdict row's `input_tokens == 100` (one call's usage — "no double token spend recorded") |
| overlap serialises | `tests/test_job_idempotency.py::test_overlapping_attempts_serialise_on_the_row_lock_and_the_loser_skips` | `BlockingFakeLLMClient([VALID4])` shared by two `TriagePipeline`s; task A `= triage_attempt(session_a, aid)`; `await started.wait()`; task B `= triage_attempt(session_b, aid)`; `await asyncio.sleep(0.3)`: `len(llm.calls) == 1` and B not done (blocked in the DB); `release.set()`; `await asyncio.gather(A, B)` → `{r.status for r in results} == {"triaged", "skipped"}` (set — order-independent); 1 verdict; `len(llm.calls) == 1` |
| cancel mid-LLM, immediate re-run | `tests/test_job_idempotency.py::test_cancelled_run_is_re_run_by_the_next_worker_with_one_verdict` | enqueue; `worker1 = Worker(..., burst=True, handle_signals=False, ...)` with the blocking fake; `t = asyncio.create_task(worker1.async_run())`; `await started.wait()`; `worker1.handle_sig(signal.SIGTERM)` (ARQ's own graceful path: cancels every running job task and the poll loop — `run_job`'s cancel branch logs "cancelled, will be run again", deletes the in-progress key and leaves the job queued); `await asyncio.gather(t, return_exceptions=True)`; `await worker1.close()`; assert 0 verdicts, status `pending`, `zcard == 1` (still queued), `await arq_redis.exists("arq:in-progress:" + triage_job_id(aid)) == 0`, `worker1.jobs_retried == 1`; `worker2` (a fresh non-blocking fake `[VALID4]` in a fresh pipeline) burst → 1 verdict, `triaged`, `worker2.jobs_complete == 1`; the verdict's `input_tokens == 100`. Assumption stated: the blocking fake never returned, so its usage was never persisted; `release` is set in `finally` |
| crash mid-LLM, lease held then expired | `tests/test_job_idempotency.py::test_crashed_run_waits_for_the_lease_then_re_runs_once` | as above up to `worker1.close()`; then **re-create** the lease as a SIGKILL would have left it: `await arq_redis.psetex("arq:in-progress:" + triage_job_id(aid), 1500, b"1")` and assert `await Job(triage_job_id(aid), arq_redis, _queue_name=TRIAGE_QUEUE_NAME).status() is JobStatus.in_progress`; `t0 = perf_counter()`; `worker2` burst (`poll_delay=0.05`) → `perf_counter() - t0 >= 1.4` (the job was "already running elsewhere" until the lease expired — burst mode keeps polling while the job is still queued), `worker2.jobs_complete == 1`, 1 verdict, `triaged`, `len(fake2.calls) == 1`. Assumption stated: the lease TTL (1.5 s) is the only thing the second worker waits on |
| crash after commit, before finish | `tests/test_job_idempotency.py::test_run_after_a_committed_but_unfinished_job_skips` | run `triage_alert_job(ctx, str(aid))` directly → `"triaged"` (commit happened; ARQ never finished anything because there was no worker); now enqueue the same id (`enqueue_triage` → `True`: no kept result exists) and run a burst `Worker` → job result `"skipped"`, `jobs_complete == 1`, 1 verdict, `len(llm.calls) == 1` |
| failed alerts are not re-run | `tests/test_job_idempotency.py::test_failed_alert_is_skipped_not_re_triaged` | `seed_alert(status="failed")`, enqueue, burst → `"skipped"`, 0 verdicts, `len(llm.calls) == 0` (M8 must flip the status first — the docstring cites the Interfaces note) |
| nothing dirty before the loop | `tests/test_triage_alert.py::test_no_pending_orm_state_before_the_tool_loop` | a local `SessionSpyTool` (defined in the test module; not one of the task-03 lifted helpers) records `ctx.session.dirty`, `ctx.session.new` and `ctx.session.in_transaction()` when called; after the run: both sets were empty and `in_transaction()` was `True` (the lock's transaction was open) |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2; the **implementer** does Steps 3–6.

- [ ] **Step 1 (RED — test-author): write `tests/test_job_idempotency.py` and the re-opened pins**
  per the table. The blocking fake and both `asyncio.Event`s live in the test module. Every
  gather/cancel path has a `try/finally` that sets `release` and closes the workers so a failing
  assertion cannot hang the suite (pytest-timeout's 120 s is the backstop, not the plan).
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_alert_service.py tests/test_triage_alert.py tests/test_job_idempotency.py` →
  Expected: `ImportError: cannot import name 'get_alert_for_update'`; the double-invocation test
  fails with **2 verdict rows** (today's `triage_attempt` has no status check — the load-bearing
  RED); the overlap test fails with `len(llm.calls) == 2`. Pin, commit `test(worker,core):
  idempotent job execution RED (m5 task-04)`.
- [ ] **Step 3 (GREEN — implementer): `get_alert_for_update` + `triage_attempt`'s lock/skip.**
  `uv run mypy` clean; the table green.
- [ ] **Step 4 (implementer): docstrings** (`worker/jobs.py` `"skipped"` + the M8 note;
  `worker/triage.py` the lock rationale), README sentence.
- [ ] **Step 5 (implementer): live kill rehearsal** against the dev stack per the procedure above
  (ids and counts pasted; never a payload). If the strong model is not configured yet the walk
  still works — routing is off.
- [ ] **Step 6 (implementer): full gates (cold) → commit** `feat(worker,core): idempotent triage
  attempts under a row lock, skip when not pending (m5 task-04)` with the two trailers;
  path-scoped `git add`. Then amend the spine's acceptance-walk row for the kill with the measured
  re-run delay in a separate `docs(plans)` commit.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_alert_service.py tests/test_triage_alert.py tests/test_job_idempotency.py tests/test_worker_job.py tests/test_worker_job_retry.py   # all pass, 0 skipped
grep -n "with_for_update" core/services/alerts.py | wc -l     # 1
grep -n "get_alert_for_update" worker/triage.py | wc -l       # 1 (the attempt) — get_alert stays for every other reader
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
```

## Acceptance

- Running a triage job twice — sequentially, overlapping, after a cancellation, after a crash
  with the ARQ lease held or expired, or after a commit ARQ never saw finish — produces exactly one
  verdict row and one recorded token spend for the alert; the second run reports `skipped`
  before any LLM call.
- The row lock is taken before the first LLM call and released with the attempt's commit or
  rollback; nothing is written before `persist_verdict`; no ORM state is dirty before the loop.
- The live kill procedure is documented with its lease timing and was rehearsed on the dev stack
  with `count(*) = 1` pasted; M8's retriage knows to flip the status before enqueueing.
