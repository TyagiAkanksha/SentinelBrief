# m7-eval-hardening — Eval hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M7 — **authoritative.** Primary sections: §7.1 (v2: ≥200 real, stratified,
**hand-labeled by the author**, 10 % re-review hygiene), §7.2 (recorded tool fixtures), §7.3
(the full metric set incl. per-severity P/R, confusion matrix, LLM-as-judge), §7.4 (nightly gate;
baselines from this run; thresholds not invented earlier), §7.5 (publication, worse numbers
included), §10.6 (≥5 injection cases in v2), §13 (labels are human work).
**Conventions:** `CONVENTIONS.md` §10, §13 · `.claude/rules/evals.md`.

**Goal:** golden set v2 exists as a human-labeled JSONL (the author labels; Claude builds the
sampler, the export/import format and the hygiene tooling); evals replay recorded tool results
for determinism; the LLM-as-judge scores reasoning quality; `evals.run` reports every §7.3
metric; a nightly GitHub Actions job runs the harness and fails on regression against baselines
recorded from the first full v2 run; `docs/results.md` is published with real numbers and linked
from the README; a deliberately worsened prompt demonstrably fails CI.

**Architecture:** `evals/sample.py` exports a stratified candidate set from the live DB (by
category of the cheap verdict and by sensor/time) into a labeling file that hides model verdicts;
`evals/label_tool.py` is a tiny CLI the author uses to enter labels and notes and to run the 10 %
re-review; `evals/golden/v2.jsonl` is written only by that tool from the author's input. The
`ToolRecorder` (M4) records each v2 case's live tool results once into `tests/fixtures/tools/`;
`evals.run` replays them. `evals/judge.py` calls the strong model at temperature 0 with a
versioned rubric prompt (`evals/prompts/judge-v1.md`). `evals/baseline.json` holds the M7
baselines; `.github/workflows/nightly-eval.yml` runs `evals.run --gate` and exits non-zero on
PRD §7.4 conditions; `evals.run` appends a row to `docs/results.md`.

**Tech Stack:** M6 stack. No new runtime dependencies beyond a scheduled workflow.

## Global Constraints

M0–M6 Global Constraints apply verbatim (branch `feat/m7-eval-hardening`). Additionally:

- **No agent writes, edits, or infers a v2 label or `labeler_note`.** The labeling CLI records
  what the author types; tests assert that `v2.jsonl` rows carry a `labeled_by: human` marker
  and that nothing under `evals/` can produce one programmatically.
- Baselines are recorded from the first full v2 run **in this milestone** and committed; gate
  thresholds are exactly PRD §7.4 (exact-match drop > 3 points, critical recall < 0.90, mean cost
  +50 % without a config change) — no others.
- The judge runs at temperature 0 on the strong model; its prompt is versioned and hash-pinned
  like the triage prompts.
- `docs/results.md` is append-only; every run row carries date, git sha, prompt version, models,
  all §7.3 metrics; worse runs are published too.
- Recorded tool fixtures are committed; a v2 case whose fixture is missing fails the eval loudly
  rather than going live.
- **One category taxonomy** (M1 review carry-over): the labeling guide (`docs/labeling-guide.md`,
  new in task-01), the `/cowrie-fixture` skill, and the active prompt's category definitions must
  agree — including a `brute_force`-vs-`reconnaissance` tie-break for targeted-username sprays —
  before the author labels a single v2 row. The judge's input is the summary and tool results,
  never raw `reasoning` from another run (t4 I1).

## Tasks (briefs written at the M6 gate)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Stratified sampler + labeling file format + `label_tool` CLI (human input only) + 10 % re-review mode | `m7-eval-hardening/task-01-v2-sampler-label-tool.md` | M6 tag |
| 2 | Record tool fixtures for every v2 case; replay mode required in `evals.run`; missing-fixture failure | `m7-eval-hardening/task-02-record-tool-fixtures.md` | task-01 (labels landed) |
| 3 | LLM-as-judge: `evals/judge.py`, `judge-v1.md`, mean + `% ≤ 2`, injection-case pass rate | `m7-eval-hardening/task-03-llm-judge.md` | task-02 |
| 4 | Full §7.3 metrics: per-severity P/R, confusion matrix, escalation rate; `eval_runs` row written | `m7-eval-hardening/task-04-full-metrics.md` | task-02 |
| 5 | Nightly workflow + `--gate` + `baseline.json` recorded from the first full v2 run | `m7-eval-hardening/task-05-nightly-gate-baselines.md` | tasks 3–4 |
| 6 | `docs/results.md` publication from `evals.run`; README results link; three runs across two prompts | `m7-eval-hardening/task-06-results-publication.md` | task-5 |
| 7 | Proof: a deliberately worsened prompt version fails the nightly gate (kept on a branch, never merged) | `m7-eval-hardening/task-07-worsened-prompt-proof.md` | task-5 |

Order: 1 → (author labels ≥200 cases — calendar time, not agent time) → 2 → (3, 4) → 5 → (6, 7).
Rationale: nothing downstream is meaningful before human labels exist; determinism precedes
metrics; baselines precede the gate; publication and the negative proof close the milestone.

## Acceptance walk (PRD §12 M7)

| Clause | Demonstrated by |
|---|---|
| Golden set v2 (≥200 real, hand-labeled, ≥5 injection cases) | task-01 output: row count, strata table, `labeled_by: human` on every row, ≥5 `injection` tags; the author's re-review disagreement rate |
| LLM-as-judge; recorded tool fixtures | task-03/02 tests; a replayed run is byte-identical across two invocations |
| Nightly CI gate with baselines set from this run | task-05 workflow run link; `baseline.json` committed |
| `docs/results.md` published; README links a results table with real numbers | task-06: three rows across two prompt versions |
| A deliberately worsened prompt fails CI | task-07: failing workflow run link pasted |

## Status

planned — briefs pending (written at the M6 gate).
