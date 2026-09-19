---
id: task-05
milestone: m8-polish
depends_on: [task-04, task-02]
status: planned
spec: PRD.md §10.3 (a daily token budget checked before EVERY LLM call; when exhausted, alerts are left `pending`, not failed, and picked up after reset), §10 (cost invariants), §12 M8; `.claude/rules/worker.md` ("token budget check before every LLM call (from M8)"); `CONVENTIONS.md` §7 (the budget is a Setting; `0` = unlimited)
---

# task-05 — Daily token-budget circuit breaker in the worker + `budget_exhausted` in stats + a dashboard banner

## Goal
Before every LLM call in `worker/triage.py` (both `complete_structured` sites and the
`complete_with_tools` loop), the pipeline checks a Redis daily token counter against
`DAILY_TOKEN_BUDGET`; when the day's consumed tokens would exceed it, the call is NOT made — the
job raises a retryable `BudgetExceededError` so the alert stays `pending` (never `failed`) and ARQ
re-attempts after the UTC-midnight reset (the counter key is date-scoped and expires). After each
successful call the counter is incremented by that call's `input+output` tokens. `0` = unlimited
(dev default). `StatsOut` gains `budget_exhausted: bool` (true when today's counter ≥ budget and
budget>0) and `tokens_today`/`daily_token_budget`; the dashboard shows a banner when exhausted. The
nightly `--no-judge` flag (added in M7) is REVERTED here — with the budget breaker live, the nightly
can judge again (capped by the breaker); update `.github/workflows/nightly-eval.yml` and its pin.

## Context (read ONLY these)
- `PRD.md` §10.3, §10. `docs/plans/m8-polish.md` Global Constraints. `.claude/rules/worker.md`.
- Code: `worker/triage.py` (`run`, the LLM call sites ~:327/:344/:362 — positions shift post-rebase; find them by `complete_structured`/`complete_with_tools`), `core/errors.py` (add `BudgetExceededError(SentinelBriefError)` code `budget_exceeded`, RETRYABLE like `LLMCallError` in the ARQ retry classification), the ARQ job (`worker/jobs.py` or wherever the retry/terminal-failed logic lives — a budget error must NOT go terminal-`failed`; it re-queues), `core/config.py` (add `daily_token_budget`), `worker/` Redis client, the M4 `RedisTTLCache`/quota-backoff pattern (reuse the counter idiom), `core/schemas/alerts_read.py::StatsOut` + the stats service, `web/src/app/stats/` (banner) or the layout (a global banner component).
- Reset semantics: key `budget:{utc_date}` with `EXPIRE` to end-of-day (or a fixed 26h TTL); a new day = a fresh counter, so `pending` alerts flow again with no manual step.

## Interfaces
```python
# core/config.py
daily_token_budget: Annotated[int, Field(ge=0)] = 0   # 0 = unlimited
# core/errors.py
class BudgetExceededError(SentinelBriefError): code = "budget_exceeded"  # retryable; carries tokens_today, budget
# worker/budget.py (new)
async def check_and_would_exceed(redis, *, budget: int, would_add_estimate: int = 0) -> bool  # budget==0 → False
async def record_tokens(redis, *, tokens: int) -> int   # INCR budget:{utc_date}; EXPIRE; returns new total
# worker/triage.py: before each LLM call, if check_and_would_exceed(...) → raise BudgetExceededError; after each success, record_tokens(usage.input+output)
# core/schemas/alerts_read.py
class StatsOut: ...; budget_exhausted: bool; tokens_today: int; daily_token_budget: int
```
The ARQ retry classification must treat `BudgetExceededError` as retryable (like `LLMCallError`) so the job re-queues with backoff and the alert stays `pending`; it must NOT count against the terminal-`failed` path.

## Folds these deferred findings
- Re-enable the judge in the nightly workflow (revert M7's `--no-judge`), since the breaker now caps spend; update `tests/test_nightly_*` if any pins the flag (none should — the shape pin matches a substring; removing `--no-judge` keeps it green).
- t03 M9 (owner cost awareness) is resolved by this task existing.

## Steps (TDD outline)
- test-author: `tests/test_budget_breaker.py` (0=unlimited never blocks; budget hit → BudgetExceededError before the call, alert stays pending, counter increments after success, new UTC day resets), `tests/test_stats_budget.py` (StatsOut.budget_exhausted true/false; tokens_today). Redis + DB fixtures skip by name.
- implementer: `worker/budget.py`, the check/record calls in `run`, the error class + ARQ retry classification, the settings + `.env.example`, StatsOut + stats service, the banner (dumb component reading the flag), the nightly `--no-judge` revert.

## Verify
With `DAILY_TOKEN_BUDGET` set low on the dev stack: post alerts → they stay `pending` (not failed), `/stats` shows the banner and `budget_exhausted:true`; raise the budget / next UTC day → they triage. `docker compose` acceptance walk in the ledger.

## Acceptance
No LLM call proceeds once the day's budget is exhausted; exhausted alerts stay `pending` and resume after reset; the breaker is a Setting (`0`=unlimited); stats + banner reflect it; the nightly judges again under the cap.
