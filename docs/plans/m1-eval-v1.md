# m1-eval-v1 — Measurement before features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M1 — **authoritative.** Primary sections: §7.1 (golden set policy: v1 is
synthetic and never published), §7.2 (CLI shape), §7.3 (metrics — the M1 subset: severity
exact/±1, category accuracy, cost, latency; escalation P/R and critical recall are cheap to add
now and are), §10.6 (≥2 injection cases in v1, ≥5 in v2), §13 (v1 labels may be machine-authored;
v2 labels never).
**Conventions:** `CONVENTIONS.md` §2, §9, §10, §13 · `.claude/rules/evals.md` ·
`/cowrie-fixture`.

**Goal:** golden set v1 (20 labeled synthetic sessions covering every severity band and category,
with ≥2 injection cases) plus `python -m evals.run` scoring severity exact / within-one, category
accuracy, escalation precision/recall, critical recall, cost (mean, p95, total) and latency (p50,
p95); results print as a table; **two different prompt versions produce two comparable rows** in
one invocation.

**Architecture:** `evals/golden.py` loads and validates JSONL rows into `GoldenCase`
(`SessionAlert` + `GoldenLabel`); `evals/scoring.py` is pure functions from `CaseResult`s to
`RunMetrics` and a markdown table; `evals/run.py` drives the **real** `TriagePipeline` from M0
over every case with a concurrency semaphore, one pipeline per `--prompt` value, and writes a
per-run JSON under the gitignored `evals/results/`. Prompt v2 is a new immutable file; v1's hash
pin from M0 must keep passing.

**Tech Stack:** as M0. No new runtime dependencies.

## Global Constraints

M0 Global Constraints apply verbatim (branch is `feat/m1-eval-v1`). Additionally:

- **v1 numbers are never published.** They go in the ledger and task reports only — never in
  `docs/results.md`, the README, or a commit message.
- **v2 labels are human work.** Nothing in this milestone touches `evals/golden/v2.jsonl`.
- Evaluation runs `worker.triage.TriagePipeline` — never a reimplementation of the loop.
- Failed cases (validation error, LLM error) count in every denominator and as wrong.
- The prompt-contract test is parametrized over every `worker/prompts/triage-v*.md`; adding v2
  must keep v1's pin green.

## Tasks

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Golden v1 dataset (20 rows) + `GoldenCase` loader | `m1-eval-v1/task-01-golden-v1-loader.md` | M0 tag |
| 2 | Scoring: `CaseResult`, `RunMetrics`, `score`, `format_table` | `m1-eval-v1/task-02-scoring.md` | task-01 |
| 3 | `evals.run` CLI: `run_golden`, repeatable `--prompt`, injectable LLM, result JSON | `m1-eval-v1/task-03-evals-run-cli.md` | task-02 |
| 4 | Prompt `triage-v2`, prompt-contract tests, `docs/results.md` stub, M1 acceptance + tag | `m1-eval-v1/task-04-prompt-v2-acceptance.md` | task-03 |

Order: 1 → 2 → 3 → 4. Rationale: the loader's `GoldenCase`/`GoldenLabel` are what scoring and
the runner consume; scoring is pinned by pure unit tests before the runner wraps it; v2 comes
last because comparability is proven by the runner.

## Acceptance walk (PRD §12 M1)

| Clause | Demonstrated by |
|---|---|
| Golden set v1: 20 synthetic fixtures labeled per rubric | task-01 `test_v1_loads_20_cases`, `test_every_severity_band_present`, `test_at_least_two_injection_cases_one_via_username` |
| `evals.run` scores severity exact/±1, category accuracy, cost, latency | task-02 `score`; task-03 wiring |
| Results print as a table | task-02 `format_table`; task-03 `test_main_prints_one_row_per_prompt_version` |
| Two different prompt versions produce two comparable result rows | task-04 `test_live_two_prompt_versions_produce_two_rows` (live, key exported); output pasted into the ledger |

## Status

planned — snapshot only; git history and the ledger are authoritative.
