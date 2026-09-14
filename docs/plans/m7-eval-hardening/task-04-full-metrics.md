---
id: task-04
milestone: m7-eval-hardening
depends_on: [task-02]
status: planned
spec: PRD.md §7.3 (per-severity precision/recall; confusion matrix; the metrics already implemented at M1/M5 stay), §7.2 ("writes an `eval_runs` row"), §7.5 (all §7.3 metrics on every published row); `.claude/rules/evals.md` ("Metrics are pure functions in `evals/scoring.py` with unit tests; failed cases count in every denominator and as wrong"); M5 final review deferrals for M7: t03-M1 `Totals` value object in `worker/triage.py::run` and t03-M4 `effective_model_primary` (fold into the same M7 touch — the pipeline is re-opened for the judge's tool-trace access anyway)
---

# task-04 — Per-severity precision/recall, the 5×5 confusion matrix, category confusion, the `eval_runs` row in `evals.run`; the deferred `Totals`/`effective_model_primary` cleanups in the pipeline

## Goal

`evals/scoring.py::score` returns the complete PRD §7.3 set as pure functions of the `CaseResult`
list: `per_severity: dict[int, PR]` (precision, recall, support for each labeled band 1–5; a failed
case is a false negative for its labeled band and a false positive for nothing), `confusion:
tuple[tuple[int, ...], ...]` (5×5, rows = labeled, columns = predicted, a sixth column for
"failed"), and `category_confusion: dict[str, dict[str, int]]`; `format_table` keeps one row per
run (the wide matrix goes to the per-run JSON and to a `--matrix` stdout section, not the results
table, whose new columns are `sev4_rec`, `sev5_rec` and `sev_macro_f1`). `evals.run` writes the
`eval_runs` row when `--database-url` is given (task-06 makes it part of `--publish`; here the
writer function and its test land). The pipeline cleanups the M5 review deferred: a frozen
`Totals(input_tokens, output_tokens, cost_usd, latency_ms)` accumulator replaces the four closure
rebinds in `worker/triage.py::run`, and `TriageOutcome.effective_model_primary` replaces the three
call sites that recompute it — behaviour-preserving (the M5 routing tests are the pin).

## Files

- Modify: `evals/scoring.py` (`PR`, `per_severity`, `confusion`, `category_confusion`, `sev_macro_f1`; `COLUMNS` += `sev4_rec`, `sev5_rec`, `sev_macro_f1`), `evals/run.py` (`--matrix`; `write_eval_run_row` — moved to `evals/publish.py` by task-06 if it lands later: implementer's call, recorded), `worker/triage.py` (`Totals`, `effective_model_primary`), `docs/results.md` (header regenerated once), `README.md` (the metrics list)
- Test-author: extend `tests/test_scoring.py` (unpinned for this task), create `tests/test_eval_runs_row.py`, `tests/test_triage_totals.py`

## Interfaces

```python
@dataclass(frozen=True) class PR: precision: float; recall: float; support: int      # support = labeled count; precision = 0.0 when nothing was predicted as that band
def per_severity(results) -> dict[int, PR]                                          # keys 1..5 always present
def confusion(results) -> tuple[tuple[int, ...], ...]                               # 5 rows (labeled 1..5) × 6 columns (predicted 1..5, failed)
def category_confusion(results) -> dict[str, dict[str, int]]                        # labeled → predicted (+ "failed") counts, all seven categories as keys
def sev_macro_f1(results) -> float                                                  # mean F1 over bands with support > 0
RunMetrics += per_severity: dict[int, PR]; confusion: tuple[...]; category_confusion: dict; sev_macro_f1: float   (COLUMNS adds sev4_rec, sev5_rec, sev_macro_f1 — the matrices are NOT columns)
# worker/triage.py
@dataclass(frozen=True) class Totals: input_tokens: int = 0; output_tokens: int = 0; cost_usd: Decimal = Decimal("0"); latency_ms: int = 0; def add(self, result: LLMResult) -> Totals
TriageOutcome.effective_model_primary -> str    # the model that produced the FINAL verdict when not escalated, else model_primary — the exact expression the three sites use today (copy it, then delete the three)
```

## Interfaces → test table

| row | test | failure branch |
|---|---|---|
| per-severity | `test_scoring.py::test_per_severity_precision_recall_with_failed_as_false_negative` | a failed sev-5 case lowers sev-5 recall and no precision; bands with no labels → support 0, recall 0.0 |
| confusion | `::test_confusion_matrix_shape_and_failed_column` | 5×6; row sums == labeled counts; the failed column counts `verdict is None` |
| category | `::test_category_confusion_all_keys_present` | seven keys each side + failed |
| macro-F1 | `::test_sev_macro_f1_ignores_unsupported_bands` | hand-computed value |
| columns | `::test_columns_gain_three_severity_columns_and_table_renders` | `COLUMNS` order; `format_table` one row |
| eval_runs | `test_eval_runs_row.py::test_write_eval_run_row_persists_metrics_json` (DB fixtures) | one row; `metrics` JSON round-trips `_metrics_payload` (Decimals as strings) |
| Totals | `test_triage_totals.py::test_totals_add_is_pure_and_sums`, `::test_run_totals_equal_pre_refactor_sums` (FakeLLMClient two-call run with retry: tokens/cost/latency identical to `tests/test_triage_pipeline.py`'s expectations) | pure accumulator; behaviour preserved |
| effective model | `::test_effective_model_primary_matches_the_three_former_sites` | escalated vs not; the M5 routing tests stay green |

## Steps (TDD)

- [ ] Steps 1–2 (test-author): RED; commit `test(evals,worker): per-severity/confusion metrics, eval_runs row, Totals RED (m7 task-04)`.
- [ ] Steps 3–4 (implementer): scoring + run + pipeline cleanups + docs; full gates (the M5 routing/idempotency suites are the regression net); commit `feat(evals): per-severity P/R, confusion matrices, eval_runs row; worker Totals + effective_model_primary (m7 task-04)`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_scoring.py tests/test_eval_runs_row.py tests/test_triage_totals.py tests/test_routing.py tests/test_triage_pipeline.py tests/test_evals_run.py   # all pass, 0 skipped
grep -c "effective_model_primary" worker/triage.py evals/run.py worker/store.py   # the property + its uses; the three inline expressions gone (grep the old expression → 0)
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- Every PRD §7.3 metric is computed by a tested pure function; the matrices live in the per-run JSON and `--matrix` output; the results table gains three severity columns; the `eval_runs` row is written; the two deferred pipeline cleanups land with the M5 suites green.
