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
- Replay is **strict by default for a `v2*` golden file** (m7 task-02, PRD §13): a case whose
  fixture is missing fails that case (and the run) loudly instead of silently scoring degraded
  tool evidence — mint every fixture a v2 case can request with `evals/record.py` before scoring.
  Strict applies only to the two `{"ip"}` tools `evals/record.py` can enumerate
  (`worker.tools.STRICT_TOOL_NAMES`, ruling R26); `get_alert_history`'s `window_hours` is the
  model's free choice and always replays leniently, strict or not.
- **Fixture persistence is a fail-closed ALLOW-list** (ruling R30): `evals.record` only ever
  persists an `unavailable(reason)` result whose `reason` is in
  `worker.tools.DETERMINISTIC_REASONS` (`invalid_arguments`, `unknown_session`,
  `unknown_asset` — a tool's own argument/lookup logic, reproducible forever). Every other reason
  — fixed (`no_api_key`, `quota_exceeded`, ...) or dynamic (`IpReputationTool`'s
  `f"http_{status}"`) — is transient: the fixture is removed, the call is reported failed, and
  `ReplayToolRecorder(strict=True)` refuses to SERVE such a fixture even if hand-written to disk.
- Metrics are pure functions in `evals/scoring.py` with unit tests; failed cases count in every
  denominator and as wrong. `critical_recall` is recall over labeled severity ≥ 4 — the number
  that matters most (PRD §7.3).
- The LLM-as-judge (M7) runs on the strong model at temperature 0 with the rubric from PRD §7.3
  and is itself versioned like a prompt. `evals/judge.py` scores every judged case's reasoning
  over the summary and replayed tool results only, never another run's raw `reasoning`; judge
  spend accumulates in `RunMetrics.judge_cost_total_usd` and never touches `cost_mean_usd`.
- `docs/results.md` is **append-only** and includes runs whose numbers got worse (PRD §7.5).
  Every row carries date, git sha, prompt version, models, and all §7.3 metrics.
- CI gate thresholds (PRD §7.4) are set from the first full v2 run at M7 — do not invent them
  earlier. The baseline itself (`evals/baseline.json`) is written ONLY by
  `evals.run --write-baseline` from a real v2 run (m7 task-05) — never invented, hand-edited, or
  fabricated by any other path; `evals.gate.load_baseline` raises rather than defaulting one.
- Per-run JSON under `evals/results/` is gitignored; only the table in `docs/results.md` is
  published.
