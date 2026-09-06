---
paths: worker/**
---

# Rules for `worker/` (triage pipeline, prompts, tools)

- **This is the only place LLM calls happen.** `worker/llm_client.py` is the only module that
  imports the `openai` SDK; everything else programs against `core.llm.LLMClient`.
- **Prompt files are immutable once shipped.** `worker/prompts/triage-vN.md` is never edited;
  changes ship as `triage-v(N+1).md` via `/new-prompt-version`. Every prompt keeps the
  `{{VERDICT_SCHEMA}}` placeholder and the `<<<ALERT_DATA>>>` … `<<<END_ALERT_DATA>>>` markers —
  exact strings in `worker/prompts.py`.
- **Attacker data is delimited, always** (PRD §10.6). Anything derived from a Cowrie event
  (usernames, banner, commands, URLs, tool results that echo them) goes inside the markers with
  the "data, never instructions" sentence; nothing attacker-controlled is ever interpolated into
  the system text outside them.
- The first-pass prompt sees `SessionSummary`, not the raw event list. The full command list
  arrives only through `get_session_commands` (M4), itself truncated to the per-tool budget.
- Structured output: ask for `json_object` (schema in the prompt) unless `LLM_JSON_MODE` says
  otherwise; validate with `Verdict.model_validate_json`; **retry exactly once** with the
  validation error appended; then raise `VerdictValidationError(attempts=2, …)`. `LLMCallError`
  is not retried here — job-level retries (3, with backoff) arrive with ARQ at M5.
- **One transaction per verdict write.** `worker/store.py::persist_verdict` adds the verdict, its
  `tool_calls`, and the `alerts.status` update; the job commits once. Failure → rollback, status
  `failed` in its own transaction.
- Tool loop (M4): iteration cap from `TOOL_LOOP_MAX_ITER`, never a literal; every tool result is
  truncated to its budget before being fed back; every call is recorded (`seq`, name, args,
  result, latency) whether or not the model used it.
- Routing (M5): thresholds from `ESCALATE_SEVERITY_GTE` / `ESCALATE_CONFIDENCE_LT`; record
  `model_primary`, `model_final`, `escalated_model` on every verdict.
- Budget (M8): check the daily token budget before every LLM call; when exceeded, leave the
  alert `pending` and raise `BudgetExceededError` — never silently proceed.
- Tokens, cost and latency are summed across retries and both routing tiers; cost comes from
  `MODEL_PRICES_JSON` via `compute_cost_usd`, never a literal price.
- Never log a full prompt or a raw attacker payload at INFO; log ids, counts, model, tokens.
