# Suggestions

Enhancements and ideas noticed during implementation that are out of scope for the current
task — never scope expansion, always a follow-up note. Anything here is unscheduled until the
owner moves it into a milestone plan (`docs/plans/`).

Format: one bullet per idea, with the task that surfaced it in a trailing parenthetical.

- Extract a shared CLI helper (`_fail`/`_Parser`/`UsageError`) now copied verbatim in
  `worker/triage_one.py`, `evals/run.py`, and `scripts/seed_dev.py` into one module, at M5 per
  the m5 spine (t6-M6).
- `scripts/seed_dev.py` reports no created/skipped/failed counts when the seed loop fails
  mid-run on a DB error; report the partial counts before re-raising (t6-M5).
- Add the `server-only` package as a guard import in `web/src/lib/api/server.ts` so a future
  client-component import fails the build instead of leaking the server-only `API_URL` fetch
  path into the browser bundle — requires a new pinned dependency (t3-M8).
