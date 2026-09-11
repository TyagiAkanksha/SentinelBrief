# Suggestions

Enhancements and ideas noticed during implementation that are out of scope for the current
task — never scope expansion, always a follow-up note. Anything here is unscheduled until the
owner moves it into a milestone plan (`docs/plans/`).

Format: one bullet per idea, with the task that surfaced it in a trailing parenthetical.

- `scripts/seed_dev.py` reports no created/skipped/failed counts when the seed loop fails
  mid-run on a DB error; report the partial counts before re-raising (t6-M5).
- Add the `server-only` package as a guard import in `web/src/lib/api/server.ts` so a future
  client-component import fails the build instead of leaking the server-only `API_URL` fetch
  path into the browser bundle — requires a new pinned dependency (t3-M8).
- Normalize/canonicalize `src_ip` at ingest (the shipper or the ingest route), not only at query
  time in `get_alert_history`/`lookup_ip_reputation`, so a non-canonical stored IPv6 spelling
  can't silently miss a history match (m4 task-05).
- Rename `get_session_commands`'s `command_count` field (or say in its description that it
  counts typed input lines only) — it means something different from `summarize_session`'s
  same-named field (input+failed vs. input-only) (m4 task-02 M3).
- Bound the number of tool calls the model can request inside one loop turn (a
  `TOOL_CALLS_PER_TURN_MAX` setting; excess calls recorded as `unavailable("turn_budget_exceeded")`)
  — today only the turn count is capped (m4 N-M3, with M8's token budget).
- `core.cache.RedisTTLCache` logs one WARNING per failed `get`/`set`; gate it behind a
  once-per-transition flag (first failure after a success, and recovery) so a Redis outage does
  not flood the worker log (m5 task-05 M2).
