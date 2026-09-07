---
id: task-02
milestone: m1-eval-v1
depends_on: [task-01]
status: planned
spec: PRD.md §7.3 (metrics), §7.5 (table columns); CONVENTIONS.md §10
---

# task-02 — Scoring: `CaseResult`, `RunMetrics`, `score`, `format_table`

## Goal

Pure, unit-tested scoring: severity exact-match and within-one rates, category accuracy,
escalation precision/recall, critical recall (labeled severity ≥ 4), cost mean/p95/total, latency
p50/p95 — with failed cases counted in every denominator and as wrong — and a markdown table
formatter with a fixed column order that `docs/results.md` will reuse.

## Context (read ONLY these)

- `PRD.md` §7.3, §7.5.
- `evals/golden.py` (task-01); `core/schemas/verdict.py`.
- `.claude/rules/evals.md`.

## Files

- Create: `evals/scoring.py`, `tests/test_scoring.py`

## Interfaces

- **Consumes:** `GoldenLabel`, `Verdict`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # evals/scoring.py
  @dataclass(frozen=True)
  class CaseResult:
      case_id: str; label: GoldenLabel; verdict: Verdict | None
      input_tokens: int; output_tokens: int; cost_usd: Decimal; latency_ms: int
      error: str | None                         # set when verdict is None

  @dataclass(frozen=True)
  class RunMetrics:
      n_cases: int; n_failed: int
      severity_exact: float; severity_within_one: float; category_accuracy: float
      escalate_precision: float; escalate_recall: float; critical_recall: float
      cost_mean_usd: Decimal; cost_p95_usd: Decimal; cost_total_usd: Decimal
      latency_p50_ms: int; latency_p95_ms: int

  def percentile(values: Sequence[float], p: float) -> float: ...      # nearest-rank; empty -> 0.0
  def score(results: Sequence[CaseResult]) -> RunMetrics: ...
      # rates are over n_cases (failed = wrong); precision with zero predicted positives = 0.0;
      # critical_recall over labels with severity >= 4 (0.0 when there are none); costs over all cases (failed cost still spent)
  @dataclass(frozen=True)
  class ResultRow:
      prompt_version: str; model: str; metrics: RunMetrics
  COLUMNS: tuple[str, ...] = ("prompt", "model", "n", "failed", "sev_exact", "sev_±1", "category",
                              "esc_prec", "esc_rec", "critical_rec", "cost_mean", "cost_p95",
                              "cost_total", "lat_p50", "lat_p95")
  def format_table(rows: Sequence[ResultRow]) -> str: ...              # markdown; header = COLUMNS; rates as 0.00, costs as 6 dp, latencies as ints
  ```

## Steps (TDD)

- [ ] **Step 1: Write failing tests** in `tests/test_scoring.py` using hand-built `CaseResult`
  lists: `test_severity_exact_and_within_one` (4 cases: exact, off-by-one, off-by-two, failed →
  0.25 / 0.50), `test_category_accuracy`, `test_escalation_precision_recall` (include the
  zero-predicted-positive edge → precision 0.0), `test_critical_recall_counts_only_labeled_ge_4`,
  `test_failed_case_counts_as_wrong`, `test_cost_mean_p95_total`, `test_latency_p50_p95`,
  `test_percentile_nearest_rank_edges` (1 value, 2 values, empty), 
  `test_format_table_one_row_per_result_with_headers` (header equals `COLUMNS` joined with `|`;
  two rows → two body lines).
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: evals.scoring`.
- [ ] **Step 3: Implement `evals/scoring.py`** as pure functions; no I/O.
- [ ] **Step 4: Tests pass; `uv run mypy` clean.**
- [ ] **Step 5: Full gates → commit:** `feat(evals): scoring metrics and table (m1 task-02)`.

## Verify

```bash
uv run pytest -q tests/test_scoring.py      # 9 passed
uv run mypy --no-incremental                 # clean
```

## Acceptance

- Every §7.3 metric in scope for M1 (plus escalation P/R and critical recall) is computed by a
  pure function with an edge-case test; failed cases lower the rates; the table's header is
  `COLUMNS`.
