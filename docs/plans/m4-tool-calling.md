# m4-tool-calling — Tool calling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M4 — **authoritative.** Primary sections: §6.3 (the five tools, the
6-iteration cap, per-tool truncation), §5 (`tool_calls` trace), §6.2 (trace persisted in the
verdict transaction), §7.2 (recorded tool fixtures for deterministic evals), §9 (timeline on the
detail page), §10.6 (tool results echoing attacker strings stay inside the data markers), §13
(AbuseIPDB and MaxMind keys are the owner's; both tools ship an `{unavailable: true}` path).
**Conventions:** `CONVENTIONS.md` §2, §3, §10, §13 · `.claude/rules/{worker,tests,web}.md`.

**Goal:** all five §6.3 tools behind one registry; the model decides which to call; the worker
executes them, truncates results to a per-tool budget, feeds them back inside the data markers,
enforces the hard cap of 6 iterations then forces a final verdict; every call (`seq`, tool,
arguments, result, latency) is persisted in `tool_calls` inside the verdict transaction; the
detail page renders the trace as a timeline. Evals replay recorded tool results so runs are
deterministic.

**Architecture:** `worker/tools/` — a `Tool` Protocol (`name`, JSON-schema `parameters`,
`async run(args) -> dict`), a registry that exposes OpenAI-style tool definitions, and a
`ToolRecorder` seam that either executes live or replays `tests/fixtures/tools/<tool>/<key>.json`.
`TriagePipeline.run` grows the loop: assistant tool calls → execute → append results as
delimited tool messages → repeat ≤ `TOOL_LOOP_MAX_ITER` → final structured verdict. Tools:
`get_session_commands` (reads `alerts.raw`), `get_asset_info` (static YAML), `get_ip_geo_asn`
(MaxMind reader over `GEOIP_DB_PATH`, unavailable when unset), `lookup_ip_reputation` (AbuseIPDB
over httpx with a 24 h cache — in-process dict until Redis lands at M5, behind a cache Protocol),
`get_alert_history` (SQL over the expression index). The `Timeline` primitive renders
`tool_calls` on the detail page.

**Tech Stack:** M3 stack + `maxminddb` · `pyyaml` · recorded-fixture JSON under
`tests/fixtures/tools/`.

## Global Constraints

M0–M3 Global Constraints apply verbatim (branch `feat/m4-tool-calling`). Additionally:

- **Tool failures never raise out of `TriagePipeline.run` (M2 final review M4-a).** Inline triage persists until M5, so an exception from a tool inside `run` would 500 the ingest request and strand the alert `pending` (`worker/triage.py` catches only `VerdictValidationError | LLMCallError`). Every tool returns `{"unavailable": true, "reason": …}` (PRD §6.3/§13) instead of raising; the loop pins that with a test per tool.
- **Outcome types move with the trace (M4-b).** When the tool trace is added, `TriageOutcome` relocates to `worker/outcome.py` so `worker/store.py` drops its `TYPE_CHECKING` import of `worker.triage`; `ToolCallRecord` stays a frozen dataclass and a test pins `frozen=True`.
- **Tool tests use recorded fixtures, never live APIs** (`CLAUDE.md`); a `@pytest.mark.live`
  smoke per network tool is the only exception.
- The loop cap, per-tool budgets and cache TTL are settings, never literals.
- Tool results are placed inside `<<<ALERT_DATA>>>` … `<<<END_ALERT_DATA>>>` before being fed
  back; a tool result can never become an instruction.
- `tool_calls` rows are written by `persist_verdict` in the same transaction as the verdict —
  never separately.
- `get_alert_history` is the only tool touching the DB and does so through `core/services/`.
- Unkeyed AbuseIPDB / missing `.mmdb` return `{unavailable: true}` and log once; they never raise
  into the loop.
- **Country flag on the queue and detail IP cells (PRD §9, deferred from M3).** Lands with
  `get_ip_geo_asn`'s result: task-07 (trace timeline) also renders the flag from the geo tool
  result, escaped text + emoji, with `{unavailable}` rendering as no flag.

## Tasks (briefs written at the M3 gate)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | `Tool` Protocol, registry, per-tool truncation, `ToolRecorder` (live/replay), fixture format | `m4-tool-calling/task-01-tool-registry-recorder.md` | M3 tag |
| 2 | `get_session_commands` (from `raw`, max 40 commands + count) and `get_asset_info` (YAML) | `m4-tool-calling/task-02-session-commands-asset-info.md` | task-01 |
| 3 | `get_ip_geo_asn` over MaxMind with unavailable path; `.mmdb` fetch script for deploy | `m4-tool-calling/task-03-geo-asn.md` | task-01 |
| 4 | `lookup_ip_reputation` over AbuseIPDB with 24 h cache Protocol and quota/unkeyed stub | `m4-tool-calling/task-04-ip-reputation.md` | task-01 |
| 5 | `get_alert_history` SQL service + tool | `m4-tool-calling/task-05-alert-history.md` | task-01 |
| 6 | Tool loop in `TriagePipeline` (cap, forced final verdict) + `ToolCallRecord` persistence + `evals.run` replay mode | `m4-tool-calling/task-06-tool-loop-persistence.md` | tasks 2–5 |
| 7 | `Timeline` primitive + trace on `/alerts/[id]`; detail DTO carries `tool_calls` | `m4-tool-calling/task-07-trace-timeline.md` | task-06 |

Order: 1 → (2, 3, 4, 5 in parallel) → 6 → 7. Rationale: the registry/recorder seam is what
makes each tool independently testable; the loop integrates them; the UI consumes the persisted
trace.

## Acceptance walk (PRD §12 M4)

| Clause | Demonstrated by |
|---|---|
| All five §6.3 tools; loop cap; full trace persisted | task-01–06 tests; `test_loop_stops_at_cap_and_forces_verdict`; `test_tool_calls_persisted_in_verdict_transaction` |
| Trace timeline on detail page | task-07 component test + browser pass |
| A fixture with a successful-login session triggers `get_session_commands` and the trace renders | `fixtures/alerts/alert4.json` through the pipeline with recorded replies → `tool_calls` row named `get_session_commands`; screenshot |
| A bare port-scan alert completes with ≤1 tool call | `fixtures/alerts/alert1.json` → `len(tool_calls) <= 1` asserted in a test and shown live |

## Status

in progress — briefs written 2026-09-09 (`m4-tool-calling/task-01` … `task-07`); git history and the ledger are authoritative.
