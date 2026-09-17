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

- Modify: `evals/scoring.py` (`PR`, `per_severity`, `confusion`, `category_confusion`, `sev_macro_f1`; `COLUMNS` += `sev4_rec`, `sev5_rec`, `sev_macro_f1`), `evals/run.py` (`--matrix`, `--database-url`), Create: `evals/publish.py` (`metrics_payload`, `write_eval_run_row` — ruling R35), `worker/triage.py` (`Totals`), `worker/outcome.py` (`effective_model_primary`, ruling R36), `docs/results.md` (header regenerated once), `README.md` (the metrics list)
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

## Controller rulings carried into this task (2026-09-17, after task-03 landed — binding, exact)

- **R34 (from R3) — `COLUMNS` insertion point.** The three new columns go AFTER `lat_p95` and BEFORE `judge_mean`, so the final order is `… lat_p50, lat_p95, sev4_rec, sev5_rec, sev_macro_f1, judge_mean, judge_pct_le2, injection_pass_rate, judge_cost_total_usd`. `docs/results.md`'s header row is regenerated once to match (a task-03 test pins header == `COLUMNS`). `sev4_rec`/`sev5_rec` render `per_severity[4].recall` / `[5].recall` with the table's existing float formatting.
- **R35 (from R4) — `evals/publish.py` is CREATED here** with two public names; task-06 extends the module, nothing moves later:
  - `def metrics_payload(metrics: RunMetrics) -> dict[str, Any]` — the JSON-safe view, moved from `evals/run.py::_metrics_payload` (run.py imports it; the private name disappears). New fields serialize as: `per_severity` → `{"1": {"precision": float, "recall": float, "support": int}, …, "5": …}` (string keys — JSON), `confusion` → list of 5 lists of 6 ints, `category_confusion` → dict of dicts, `sev_macro_f1` → float; Decimals stay strings as today.
  - `async def write_eval_run_row(session: AsyncSession, *, git_sha: str | None, prompt_version: str, model_config: dict[str, Any], started_at: datetime, metrics: RunMetrics) -> uuid.UUID` — inserts one `EvalRunRow` with `metrics=metrics_payload(metrics)`; `flush()`, never `commit()` (CONVENTIONS §3 — the caller owns the transaction).
  - `evals.run` gains `--database-url URL` (optional). When given: after each prompt version is scored, open ONE session from `core.db.make_engine`/`make_session_factory` exactly as `evals/sample.py:322` does, call `write_eval_run_row(...)` with `model_config = {"model": model, "judge_model": <judge model or "">, "replay_strict": bool, "judge": bool}` (the effective run config), `commit()`, and print `eval_runs: <uuid> (<prompt_version>)` on stdout after the table. Without the flag no DB code runs and no DB import is exercised at call time — the existing DB-less `tests/test_evals_run.py` stays green. The sampler's `Settings().database_url` fallback is NOT copied here: `evals.run` writes only when told to.
- **R36 — `effective_model_primary` lives on `TriageOutcome` in `worker/outcome.py`** (that is where the class is; this brief's "worker/triage.py" for this item is corrected). Expression: `self.model_primary or self.model`. Call sites replaced: `worker/triage.py:466` (`persist_verdict(... model_primary=...)`) and `worker/triage_one.py:120` (the CLI JSON); the docstring at `worker/outcome.py:56` is reworded. Verify: `grep -rn "model_primary or" api worker core evals` → 0 lines.
- **R37 — `Totals` shape.** `worker/triage.py`: `@dataclass(frozen=True) class Totals` with the four fields defaulting to zero and two pure methods — `add(self, result: LLMResult[Any]) -> Totals` (usage + cost + latency of one successful call) and `add_error(self, err: StructuredOutputError) -> Totals` (the validation-failure path at `worker/triage.py:278-282` also accumulates). The strong-pass reset at `worker/triage.py:393-396` becomes `totals = Totals(cheap.input_tokens, cheap.output_tokens, cheap.cost_usd, cheap.latency_ms)`; `_outcome` reads `totals`; the four `nonlocal` rebinds and the four bare accumulators disappear. `test_totals_add_is_pure_and_sums` covers BOTH methods (inputs unchanged, sums exact, `Decimal` cost).
- **R38 — `RunMetrics` field placement.** The four new fields (`per_severity`, `confusion`, `category_confusion`, `sev_macro_f1`) are inserted BEFORE the defaulted judge fields (`judge_mean` …) as NON-default fields. Two existing tests build `RunMetrics(...)` by hand (`tests/test_scoring.py:375,395`, M1-era `format_table` pins); the test-author extends those two constructor calls with the four new keyword arguments (values: `per_severity={b: PR(0.0, 0.0, 0) for b in range(1, 6)}`, `confusion=((0,)*6,)*5`, `category_confusion={}`, `sev_macro_f1=0.0`) — that edit is part of this task's RED and is the only permitted touch of M1 assertions (they are not weakened: the rendered row still asserts the same columns plus the three new ones).
- **Scoring rules (pinned by the test table):** `per_severity[b].recall = TP_b / support_b` (0.0 when support 0); `precision_b = TP_b / predicted_b` (0.0 when nothing predicted b); a failed case (`verdict is None`) is a false negative for its labeled band and counts in NO predicted column except the sixth `failed` column; `sev_macro_f1` = mean of `2PR/(P+R)` (0.0 when P+R == 0) over bands with `support > 0`, 0.0 when no band has support; `category_confusion` keys: all seven `VerdictCategory` values on the labeled side, each mapping to all seven plus `"failed"` on the predicted side, ints, zeros present.
- **Interfaces → test table amendments:** `test_totals_add_is_pure_and_sums` also covers `add_error`; add `test_scoring.py::test_metrics_payload_round_trips_new_fields` (in `tests/test_eval_runs_row.py` if the author prefers — it is DB-free: `json.loads(json.dumps(metrics_payload(score(results))))` has string band keys and 5×6 lists); `test_eval_runs_row.py::test_write_eval_run_row_persists_metrics_json` uses the `db_session` fixture, asserts one `EvalRunRow` with `metrics["per_severity"]["4"]["recall"]` and `metrics["cost_total_usd"]` as a string; `tests/test_evals_run.py` gains `test_main_writes_eval_runs_row_when_database_url_given` (DB fixtures; `--database-url` = the fixture engine's URL with the `tmp_schema` search path — reuse the pattern `tests/test_sample.py` uses for the sampler's `--database-url`/`--schema`; if `evals.run` needs a `--schema` flag for the test schema, add it with the sampler's semantics) and `test_main_without_database_url_touches_no_db` (FakeLLMClient run; assert `write_eval_run_row` is never called via monkeypatch).

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
