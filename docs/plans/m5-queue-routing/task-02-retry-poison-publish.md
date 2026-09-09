---
id: task-02
milestone: m5-queue-routing
depends_on: [task-01]
status: planned
spec: PRD.md §6.2 (retry up to 3 times with backoff; unrecoverable → `status='failed'`, log, move on; a poison alert must never wedge the queue; publish `verdict.created` on Redis pub/sub after the commit; the retry-budget bound `(TOOL_LOOP_MAX_ITER + 2) × 3`), §8 (`GET /api/v1/stream` is fed by that pub/sub — M8 consumes it), §10.3 (every retried call counts against the budget — M8); CONVENTIONS.md §3 (the job owns its commit), §4 (typed errors; a new carve-out lands in CONVENTIONS in the same round — M4 plan-defect rule 12), §7 (retry count and backoff are settings); `.claude/rules/worker.md` (job-level retries: 3, with backoff); M5 spine Global Constraints "Terminal state on unexpected exceptions" (`pending` must never be a resting state once the queue exists)
---

# task-02 — Retry with exponential backoff, terminal `failed` after the last try (any exception family), poison alerts never wedge the queue, `verdict.created` published after the commit; `TriagePipeline.triage_attempt` (raising) beside `triage_alert`

## Goal

`worker/jobs.py::triage_alert_job` becomes the PRD §6.2 job: one attempt per ARQ try
(`TriagePipeline.triage_attempt`, which raises on failure after rolling its own writes back); on
any `Exception` the pure policy `worker/retry.py::decide_retry` says either **retry** (`raise
arq.worker.Retry(defer=backoff_seconds(job_try))`, exponential from
`TRIAGE_JOB_BACKOFF_BASE_S`, capped at `TRIAGE_JOB_BACKOFF_MAX_S`) or, on the last allowed try
(`job_try >= TRIAGE_JOB_MAX_TRIES`), **fail**: the alert is marked `failed` in its own fresh
transaction, the exception class (and `code` for a `SentinelBriefError`) is logged with the ids,
and the job returns `"failed"` normally so ARQ finishes it and the queue keeps draining. A missing
alert (`NotFoundError`) is never retried (`"missing"`). Nothing is caught below `Exception`:
`asyncio.CancelledError` propagates so ARQ's cancel/abort/re-run semantics (task-04) hold. The
attempt count is interpreted as **total attempts = 3** (the first run plus two retries) — this is
the number PRD §6.2's cost bound multiplies by; the PRD's "retried up to 3 times" wording is
amended to say so (v1.4). After a successful commit the job publishes one `verdict.created`
message on the Redis channel `sentinelbrief:verdict.created` (M8's SSE feed); publishing is
best-effort — a Redis failure there is a WARNING, never a raise, because the verdict is already
durable. `ARQ`'s own `max_tries` is set from the same Setting as a belt-and-braces guard the job
never actually trips.

## Context (read ONLY these)

- `PRD.md` §6.2 (both paragraphs), §8 (`/stream` row), §15 (changelog format).
- `docs/plans/m5-queue-routing.md` — Global Constraints (terminal state; retry count/backoff are
  settings; the poison bound is the number this design is written against).
- `CONVENTIONS.md` §3, §4 (the two existing carve-outs — this task adds the third in the same
  round), §7; `.claude/rules/{worker,tests}.md`.
- Task-01 outputs: `worker/jobs.py` (`triage_alert_job`, `JobResult`), `worker/main.py`
  (`WorkerSettings`, `startup`/`shutdown`), `core/queue.py` (names), `tests/conftest.py`
  (`redis_url`, `arq_redis`), `tests/test_worker_job.py` (the burst-worker pattern — re-opened
  here), `tests/test_triage_alert.py` (the direct `triage_alert` pins — re-opened here).
- Code you build on: `worker/triage.py::triage_alert` (its body becomes `triage_attempt` +
  the M2 terminal branch), `worker/store.py::persist_verdict`, `core/services/alerts.py`,
  `core/schemas/alerts_read.py::reasoning_excerpt` (the summary line in the published payload).
- ARQ 0.28 facts: `Retry(defer=<seconds>)` re-queues the job with `_defer_by`; `job_try` in
  `ctx` is 1-based and increments on every start (including a re-run after a crash, so crash
  re-runs consume tries too); ARQ refuses to run a job whose `job_try > max_tries` and marks it
  failed WITHOUT calling the function — which is exactly why the job must own the terminal write on
  `job_try == max_tries` rather than raising `Retry` one more time. In burst mode the worker keeps
  polling while a deferred job is still in the queue, so a deferred retry runs inside the same
  `await worker.main()`.

## Files

- Create: `worker/retry.py`, `worker/publish.py`
- Create (test-author): `tests/test_retry_policy.py`, `tests/test_worker_job_retry.py`,
  `tests/test_publish.py`
- Modify (test-author, re-pinned): `tests/test_worker_job.py`
  (`test_job_failure_marks_the_alert_failed` → the retry-aware version below),
  `tests/test_triage_alert.py` (+ `triage_attempt` pins)
- Modify: `worker/jobs.py`, `worker/triage.py`, `worker/main.py` (`max_tries`),
  `core/queue.py` (`VERDICT_CREATED_CHANNEL`), `core/config.py`, `.env.example`,
  `CONVENTIONS.md` §4 (third carve-out), `PRD.md` (§6.2 wording + `Version: 1.4` header + §15
  entry)

## Interfaces

- **Consumes:** `triage_alert_job`, `JobResult`, `WorkerSettings` (task-01); `persist_verdict`;
  `get_alert`, `set_alert_status`; `reasoning_excerpt`; `arq.worker.Retry`; `FakeLLMClient`.
- **Produces (tasks 03–05 rely on — produce exactly):**

  ```python
  # core/config.py — new fields (+ .env.example lines under "Queue & worker (from M5)")
  triage_job_max_tries: Annotated[int, Field(ge=1)] = 3               # TRIAGE_JOB_MAX_TRIES=3 — TOTAL attempts (first run + retries); PRD §6.2's "× 3"
  triage_job_backoff_base_s: Annotated[float, Field(ge=0)] = 2.0      # TRIAGE_JOB_BACKOFF_BASE_S=2 — delay before retry 1; doubles each retry
  triage_job_backoff_max_s: Annotated[float, Field(ge=0)] = 60.0      # TRIAGE_JOB_BACKOFF_MAX_S=60 — cap on that delay

  # core/queue.py
  VERDICT_CREATED_CHANNEL = "sentinelbrief:verdict.created"

  # worker/retry.py — pure; no I/O, no logging
  def backoff_seconds(job_try: int, *, base_s: float, max_s: float) -> float: ...
      # job_try < 1 -> ValueError; min(base_s * 2 ** (job_try - 1), max_s)
      # worked table at the defaults (base 2, max 60): try 1 -> 2.0, 2 -> 4.0, 3 -> 8.0, 4 -> 16.0, 5 -> 32.0, 6 -> 60.0 (64 capped), 7 -> 60.0; base 0 -> 0.0 always
  @dataclass(frozen=True)
  class RetryDecision:
      action: Literal["retry", "fail"]
      defer_s: float                  # 0.0 when action == "fail"
      reason: str                     # exc.code for a SentinelBriefError, else type(exc).__name__ — the string that is logged
  def decide_retry(exc: BaseException, *, job_try: int, max_tries: int, base_s: float, max_s: float) -> RetryDecision: ...
      # job_try < 1 or max_tries < 1 -> ValueError
      # job_try >= max_tries -> RetryDecision("fail", 0.0, reason)
      # else                 -> RetryDecision("retry", backoff_seconds(job_try, base_s=base_s, max_s=max_s), reason)
      # The policy is family-blind on purpose: LLMCallError (transient transport/HTTP), VerdictValidationError (a poison reply may be
      # deterministic, but PRD §6.2's bound budgets exactly these retries) and any other Exception (DB blip, tool code bug that escaped the
      # registry backstop) all retry until the last try. NotFoundError never reaches decide_retry (the job handles it first).

  # worker/triage.py
  @dataclass(frozen=True)
  class AttemptResult:
      status: Literal["triaged", "skipped"]     # "skipped" is minted by task-04; declared now
      verdict_id: uuid.UUID | None
      outcome: TriageOutcome | None
  async def triage_attempt(self, session: AsyncSession, alert_id: uuid.UUID) -> AttemptResult: ...
      # row = await get_alert(session, alert_id)          (NotFoundError propagates)      — task-04 swaps this for the FOR UPDATE load
      # alert = SessionAlert.model_validate(row.raw)
      # try:
      #     outcome = await self.run(alert, session=session)
      #     verdict_id = await persist_verdict(session, alert_id=alert_id, outcome=outcome, model_primary=self._model, tool_calls=outcome.tool_calls)
      #     await session.commit()
      # except BaseException:            # includes CancelledError: the rollback must run on cancellation too, then re-raise unchanged
      #     await session.rollback()
      #     raise
      # return AttemptResult(status="triaged", verdict_id=verdict_id, outcome=outcome)
      # Load → run → write: no ORM object is modified before `run` completes (M4 task-05 M7: get_alert_history's SAVEPOINT must never autoflush pending state).
  async def triage_alert(self, session, alert_id) -> AlertStatus: ...
      # UNCHANGED contract (seed_dev + the M2 pins): try: (await self.triage_attempt(session, alert_id)).status -> "triaged"
      # except (VerdictValidationError, LLMCallError) as exc: await set_alert_status(session, alert_id, "failed"); await session.commit(); logger.warning(... code=%s); return "failed"
      # (a "skipped" AttemptResult is returned as "triaged" here — its alert already IS triaged; task-04 documents this)

  # worker/publish.py
  def verdict_created_payload(*, alert_id: uuid.UUID, verdict_id: uuid.UUID, verdict: Verdict) -> dict[str, Any]: ...
      # {"alert_id": str, "verdict_id": str, "severity": int, "category": str, "escalate": bool, "summary": reasoning_excerpt(verdict.reasoning)}
      # — PRD §8's "(id, severity, category, summary line)"; `summary` is model text derived from attacker data: the SSE consumer (M8) renders it as text
  async def publish_verdict_created(redis: Redis, *, alert_id: uuid.UUID, verdict_id: uuid.UUID, verdict: Verdict) -> bool: ...
      # await redis.publish(VERDICT_CREATED_CHANNEL, json.dumps(payload)); return True
      # redis.exceptions.RedisError | OSError -> logger.warning("verdict.created publish failed alert_id=%s verdict_id=%s exc=%s", ..., type(exc).__name__); return False
      # NEVER raises: the verdict is already committed; SSE is best-effort.

  # worker/jobs.py — the job after this task
  async def triage_alert_job(ctx: Mapping[str, Any], alert_id: str) -> JobResult: ...
      # alert_uuid = uuid.UUID(alert_id)                                   (ValueError propagates — a bug, never retried)
      # pipeline, factory, settings = ctx["pipeline"], ctx["session_factory"], ctx["settings"]
      # job_id = ctx.get("job_id"); job_try = int(ctx.get("job_try") or 1)
      # try:
      #     async with factory() as session:
      #         attempt = await pipeline.triage_attempt(session, alert_uuid)
      # except NotFoundError:
      #     logger.warning("triage job: alert not found alert_id=%s job_id=%s", alert_id, job_id); return "missing"
      # except Exception as exc:                                             # the THIRD documented carve-out (CONVENTIONS §4): never BaseException
      #     decision = decide_retry(exc, job_try=job_try, max_tries=settings.triage_job_max_tries,
      #                             base_s=settings.triage_job_backoff_base_s, max_s=settings.triage_job_backoff_max_s)
      #     if decision.action == "retry":
      #         logger.warning("triage job retry alert_id=%s job_id=%s try=%d/%d reason=%s defer_s=%.1f", alert_id, job_id, job_try, max_tries, decision.reason, decision.defer_s)
      #         raise Retry(defer=decision.defer_s) from exc
      #     log_call = logger.warning if isinstance(exc, SentinelBriefError) else logger.error       # unexpected families keep their traceback
      #     log_call("triage job failed alert_id=%s job_id=%s try=%d/%d reason=%s", ..., exc_info=not isinstance(exc, SentinelBriefError))
      #     await mark_alert_failed(factory, alert_uuid)                     # its own fresh session + commit; if THIS raises, the exception propagates,
      #     return "failed"                                                  #   ARQ records the job as failed and the alert rests `pending` — the one documented residual
      # if attempt.status == "triaged" and attempt.verdict_id is not None and attempt.outcome is not None:
      #     await publish_verdict_created(ctx["redis"], alert_id=alert_uuid, verdict_id=attempt.verdict_id, verdict=attempt.outcome.verdict)
      # return attempt.status
  async def mark_alert_failed(factory: async_sessionmaker[AsyncSession], alert_id: uuid.UUID) -> None: ...
      # async with factory() as session: await set_alert_status(session, alert_id, "failed"); await session.commit()
  # Log lines carry ids, try counters, reason strings and class names only — never the payload, a prompt, or an exception message that may embed one.

  # worker/main.py — WorkerSettings gains: max_tries = settings.triage_job_max_tries
  ```

  **Cost arithmetic (rule 3):** with `TRIAGE_JOB_MAX_TRIES=3` and `TOOL_LOOP_MAX_ITER=6`, a
  poison alert costs at most `(6 + 2) × 3 = 24` LLM calls (six tool turns + the forced final +
  the one validation retry, three times) — exactly PRD §6.2's bound; task-03 adds the strong tier's
  two calls to that sentence. A `FakeLLMClient` scripted `["{}", "{}"] * 3` (tool-less) is consumed
  in full by three attempts: 2 calls per attempt × 3 = 6 calls.

  **PRD v1.4 amendment (docs step):** §6.2 second paragraph, "the job itself is retried up to 3
  times (M5)" → "the job is attempted at most `TRIAGE_JOB_MAX_TRIES` = 3 times in total — the first
  run plus two retries with exponential backoff (M5)"; `Version: 1.4` in the header; §15 gains
  `**v1.4 — <date>.** M5 build-time amendment; no scope change.` with a bullet for this wording
  (task-03 appends its own bullet to the same entry).

  **CONVENTIONS §4 (rule 12):** add the third carve-out sentence: `worker/jobs.py::triage_alert_job`
  (M5) catches `Exception` — never `BaseException` — around the whole attempt so a poison alert
  can never wedge the queue (PRD §6.2); the decision is delegated to the pure `worker/retry.py`,
  the terminal write is `failed`, and the log carries `reason=<code | ExceptionClass>` only.

## Interfaces → test table

`tests/test_worker_job_retry.py` builds the burst `Worker` exactly as task-01's
`tests/test_worker_job.py` does, with `ctx["settings"] = Settings(triage_job_max_tries=3,
triage_job_backoff_base_s=0.01, triage_job_backoff_max_s=0.05, …)` so deferred retries run within
milliseconds (rule 7: the real backoff numbers are pinned by the pure tests, not by sleeping) and
`max_tries=3` passed to the `Worker` too. The poison test alone uses `base_s=0.0` (see its row:
a zero deferral keeps ARQ's queue order deterministic). Tests never put `"redis"` in the `ctx`
they hand a `Worker` — ARQ sets `ctx["redis"]` itself before the first job; only the tests that
call `triage_alert_job` directly put a fake Redis (`publish` recorded or raising) under that key.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| backoff table | `tests/test_retry_policy.py::test_backoff_doubles_from_base_and_caps_at_max` | the worked table above, parametrized (tries 1–7 at base 2 / max 60; base 0 → 0.0); `job_try=0` → `ValueError` |
| decide: retry | `tests/test_retry_policy.py::test_decide_retries_below_max_tries_with_backoff` | `LLMCallError`, `VerdictValidationError(attempts=2, last_error="x")`, `RuntimeError("boom")` at `job_try=1, max_tries=3` → `("retry", 2.0, "llm_call_failed" / "verdict_validation" / "RuntimeError")`; at `job_try=2` → `defer_s == 4.0` |
| decide: fail | `tests/test_retry_policy.py::test_decide_fails_on_the_last_try` | `job_try=3, max_tries=3` → `("fail", 0.0, reason)`; `job_try=4, max_tries=3` (a crash re-run past the cap) → `"fail"`; `max_tries=1` → `job_try=1` fails immediately (no retries when the Setting is 1) |
| decide: bounds | `tests/test_retry_policy.py::test_decide_rejects_nonpositive_counters` | `job_try=0` / `max_tries=0` → `ValueError` |
| transient then success | `tests/test_worker_job_retry.py::test_llm_call_errors_retry_with_backoff_then_succeed` | `FakeLLMClient([LLMCallError("a"), LLMCallError("b"), VALID4])` → status `triaged`, 1 verdict, `worker.jobs_retried == 2`, `worker.jobs_complete == 1`, `len(llm.calls) == 3`; caplog has two `triage job retry … try=1/3 … defer_s=0.0` / `try=2/3 … defer_s=0.0`-shaped WARNINGs (base 0.01 → 0.0 after `%.1f`) naming `reason=llm_call_failed`; the alert's status never rests at `failed` in between (read after the run only — rule 9: no ordering assumption beyond counts) |
| poison → failed, queue drains | `tests/test_worker_job_retry.py::test_poison_alert_fails_after_max_tries_and_the_queue_keeps_draining` | alert A enqueued FIRST, then alert B; one shared fake scripted `["{}", "{}", "{}", "{}", "{}", "{}", VALID4]`; this test alone uses `triage_job_backoff_base_s=0.0` and `max_jobs=1`. **Ordering assumption (rule 9), stated in the docstring:** ARQ hands out runnable jobs in ascending queue-score order and a `Retry(defer=0)` leaves A's score untouched (older than B's), so with one job slot A's three attempts all run before B — the fake's queue is consumed in exactly that order. → A ends `failed` with 0 verdicts, B ends `triaged` with 1 verdict; `jobs_retried == 2`, `jobs_complete == 2`, `jobs_failed == 0`, `zcard == 0`; `len(llm.calls) == 7`. **Computed:** attempt 1 = calls 1–2, attempt 2 = 3–4, attempt 3 = 5–6 (each `VerdictValidationError`), B = call 7 |
| unexpected exception family | `tests/test_worker_job_retry.py::test_unexpected_exception_retries_then_fails_with_the_class_logged` | `FakeLLMClient([RuntimeError("boom")] * 3)` → `failed`, `jobs_retried == 2`; the terminal log record is ERROR level with `reason=RuntimeError` and `exc_info` set; no `"boom"` requirement either way (message text is not the contract) |
| missing never retried | `tests/test_worker_job_retry.py::test_missing_alert_is_not_retried` | enqueue `uuid4()` → `"missing"`, `jobs_retried == 0` (carried from task-01, now with the new except order: `NotFoundError` is checked before `Exception`) |
| terminal write itself fails | `tests/test_worker_job_retry.py::test_failed_terminal_write_propagates_and_arq_records_the_failure` | a `session_factory` stub (a plain callable — the DB is the external seam) that returns real `db_session_factory()` sessions for calls 1–3 and, on call **4**, a session from `make_session_factory(make_engine("postgresql://sentinel:sentinel@127.0.0.1:1/nope"))`; `FakeLLMClient([LLMCallError("x")] * 3)`. **Computed:** 3 attempts + 1 terminal write = 4 factory calls. → `worker.jobs_failed == 1`, `jobs_complete == 0`; the alert's status is still `pending` (the documented residual); the worker's `main()` returned normally (no crash). Dispose the dead engine in `finally` |
| ARQ max_tries mirrors the Setting | `tests/test_worker_job_retry.py::test_worker_settings_max_tries_is_the_setting` (imports `worker.main` with env `TRIAGE_JOB_MAX_TRIES=4`) | `WorkerSettings.max_tries == 4` |
| publish after commit | `tests/test_worker_job_retry.py::test_successful_job_publishes_verdict_created_once` | subscribe `arq_redis.pubsub()` to `VERDICT_CREATED_CHANNEL` before running the burst worker; exactly one message; `json.loads(data) == {"alert_id": …, "verdict_id": <the row's id>, "severity": 4, "category": "successful_intrusion", "escalate": True, "summary": reasoning_excerpt(...)}`; a `failed` job publishes nothing (second half of the test with the poison fake, same subscription: still one message) |
| publish never raises | `tests/test_publish.py::test_publish_failure_is_a_warning_not_a_raise` | a fake Redis whose `publish` raises `redis.exceptions.ConnectionError("down")` → returns `False`; one WARNING naming `exc=ConnectionError` and both ids; and the job path: `test_worker_job_retry.py::test_publish_failure_does_not_fail_the_job` — `ctx["redis"]` cannot be swapped under a real Worker, so this one calls `triage_alert_job(ctx_with_fake_redis, str(aid))` directly → `"triaged"`, 1 verdict, WARNING logged |
| payload shape | `tests/test_publish.py::test_verdict_created_payload_shape` | keys exactly `{"alert_id", "verdict_id", "severity", "category", "escalate", "summary"}`; `summary` is `reasoning[:160]` (`REASONING_EXCERPT_CHARS`); ids are strings |
| `triage_attempt` raises after rollback | `tests/test_triage_alert.py::test_triage_attempt_rolls_back_and_raises_on_failure` | a dirty row in the same session (the M2 `rollback-b-002` pattern) + `FakeLLMClient(["{}", "{}"])` → `VerdictValidationError`; the dirty row is gone in a fresh session; the alert is still `pending` (the attempt does NOT mark `failed` — that is the job's decision) |
| `triage_attempt` commits | `tests/test_triage_alert.py::test_triage_attempt_returns_verdict_id_and_commits` | `AttemptResult(status="triaged", verdict_id=<uuid>, outcome=<TriageOutcome>)`; a fresh session sees the verdict row with that id |
| `triage_alert` unchanged | `tests/test_triage_alert.py` (existing four pins) | still green — the M2 contract stands |
| task-01 file re-pinned | `tests/test_worker_job.py::test_job_failure_marks_the_alert_failed` → `…_after_max_tries` | now scripts three `LLMCallError`s and asserts `failed`, `jobs_retried == 2` (the single-attempt assertion is deleted) |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 (new files + the two re-opened files, re-pinned); the
**implementer** does Steps 3–6.

- [ ] **Step 1 (RED — test-author): write the three new test files per the table; re-open
  `tests/test_worker_job.py` and `tests/test_triage_alert.py`.** Every retry test sets
  `triage_job_backoff_base_s=0.01`; the poison test uses `max_jobs=1`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_retry_policy.py tests/test_publish.py tests/test_worker_job_retry.py
  tests/test_triage_alert.py tests/test_worker_job.py` → Expected: `ModuleNotFoundError:
  worker.retry` / `worker.publish`; `AttributeError: 'TriagePipeline' object has no attribute
  'triage_attempt'`; the re-pinned job test fails because the task-01 job marks `failed` after ONE
  attempt (`jobs_retried == 0`). Pin, commit `test(worker): retry policy, poison → failed,
  verdict.created publish RED (m5 task-02)`.
- [ ] **Step 3 (GREEN — implementer): settings + `.env.example` + `core/queue.py` channel +
  `worker/retry.py`** (pure tests green). `uv run mypy` clean.
- [ ] **Step 4 (GREEN — implementer): `worker/triage.py` (`AttemptResult`, `triage_attempt`,
  `triage_alert` re-expressed over it), `worker/publish.py`, `worker/jobs.py`, `worker/main.py`
  `max_tries`.** `uv run mypy` clean; the whole table green with both env vars exported.
- [ ] **Step 5 (docs — implementer): PRD v1.4 (§6.2 wording, header, §15) and CONVENTIONS §4
  third carve-out**, in the same commit as the code (rule 12).
- [ ] **Step 6 (implementer): full gates (cold) → commit** `feat(worker): job retries with
  backoff, terminal failed on the last try, verdict.created publish (m5 task-02)` with the two
  trailers; path-scoped `git add worker core/config.py core/queue.py .env.example CONVENTIONS.md
  PRD.md`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_retry_policy.py tests/test_publish.py tests/test_worker_job_retry.py tests/test_worker_job.py tests/test_triage_alert.py   # all pass, 0 skipped
grep -n "except Exception" worker/jobs.py | wc -l        # 1 — the one documented boundary
grep -n "except BaseException" worker/ -r               # only worker/triage.py's rollback-and-re-raise
grep -n "1.4" PRD.md | head -3                          # header + §15
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
```

## Acceptance

- A failing attempt is retried with exponential backoff until `TRIAGE_JOB_MAX_TRIES` total
  attempts, then the alert is `failed` in its own transaction and the job completes normally — a
  poison alert costs at most `(TOOL_LOOP_MAX_ITER + 2) × TRIAGE_JOB_MAX_TRIES` LLM calls and the
  next job runs; `pending` rests only when the terminal write itself cannot reach the database.
- Every exception family except `NotFoundError` follows the same policy; cancellation is never
  swallowed; every log line carries ids/counters/class names only.
- A successful commit publishes one `verdict.created` message with the documented payload;
  publish failures never fail the job.
- PRD §6.2 says "attempted at most 3 times in total"; CONVENTIONS §4 documents the third
  carve-out; all three numbers are settings with `.env.example` lines.
