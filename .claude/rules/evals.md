---
paths: evals/**, fixtures/**, docs/results.md
---

# Rules for `evals/`, `fixtures/`, and `docs/results.md`

- **Golden v1 numbers are never published** (PRD §7.1). They may appear in the ledger and in
  task reports, never in `docs/results.md`, the README, or a commit message.
- **Golden v2 labels are human work** (PRD §13). Claude may write the stratified sampler, the
  export format, the loader, the scorer, the judge, and the CI gate — and may never write, edit,
  "correct", or infer a v2 label or `labeler_note`. If a task seems to require one, stop.
- v1 fixtures and labels are synthetic and may be authored here (use `/cowrie-fixture`); every
  row cites the PRD §6.6 rubric row in `labeler_note`, and every v1 injection case is tagged
  `"injection"`.
- Evaluation runs the **real pipeline** (`worker.triage.TriagePipeline`), never a re-implementation
  of it. Tool calls (M4+) replay recorded fixtures from `tests/fixtures/tools/` for determinism;
  the LLM is the only live component in an eval run.
- Metrics are pure functions in `evals/scoring.py` with unit tests; failed cases count in every
  denominator and as wrong. `critical_recall` is recall over labeled severity ≥ 4 — the number
  that matters most (PRD §7.3).
- The LLM-as-judge (M7) runs on the strong model at temperature 0 with the rubric from PRD §7.3
  and is itself versioned like a prompt.
- `docs/results.md` is **append-only** and includes runs whose numbers got worse (PRD §7.5).
  Every row carries date, git sha, prompt version, models, and all §7.3 metrics.
- CI gate thresholds (PRD §7.4) are set from the first full v2 run at M7 — do not invent them
  earlier.
- Per-run JSON under `evals/results/` is gitignored; only the table in `docs/results.md` is
  published.
