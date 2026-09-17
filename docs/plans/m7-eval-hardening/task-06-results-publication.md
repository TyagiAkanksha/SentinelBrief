---
id: task-06
milestone: m7-eval-hardening
depends_on: [task-05]
status: planned
spec: PRD.md §7.5 ("`docs/results.md` (linked from README) holds a versioned table: date, git sha, prompt version, models, and all §7.3 metrics — including runs where numbers got worse. The golden set v2 is published in-repo as a labeled dataset."), §7.2 ("writes an `eval_runs` row plus `docs/results.md`"), §12 M7 (*Accept:* "README links a results table with real numbers"); spine Global Constraints (append-only; every row carries date, git sha, prompt version, models, all §7.3 metrics; worse runs too); `.claude/rules/evals.md`; `docs/results.md` (the M1 stub: policy + the fixed column order)
---

# task-06 — `evals.run --publish` appends a row to `docs/results.md` (append-only, all §7.3 columns incl. the judge and per-severity metrics) and writes the `eval_runs` row; README links the table; three published runs across two prompt versions

## Goal

Publication becomes a mechanical act of the harness, not a hand-edit: `evals/publish.py::append_result_row(path, row)` renders one markdown table row from a `ResultRow` (task-04's extended `RunMetrics` incl. `judge_mean`, `judge_pct_le2`, `injection_pass_rate`, per-severity P/R summary columns and the confusion-matrix pointer) with the date, git sha, prompt version and the model pair, and appends it below the last row — never rewriting an existing line (a test diffs the file before/after and asserts only an append). `evals.run --publish` calls it for every `--prompt` row AND inserts the `eval_runs` row (`EvalRunRow`: git_sha, prompt_version, model_config, started_at, metrics) when `--database-url` is given (DB-less runs skip the row with a notice). `docs/results.md`'s header row is regenerated ONLY when the column set changes (task-04 changes it once; a test pins that the header equals `evals/scoring.py::COLUMNS`). The milestone's acceptance walk publishes three rows: `triage-v4` and `triage-v3` on the same day and git sha, plus a second `triage-v4` run a day later (drift visibility). The README gains the results link and a one-paragraph honest framing (PRD §1.3).

## Files

- Create: `evals/publish.py`; (test-author) `tests/test_publish.py`
- Modify: `evals/run.py` (`--publish`, `--database-url`/`--schema` for the `eval_runs` row via the `scripts/check_real_sessions.py` seam), `evals/scoring.py::COLUMNS` (task-04 extended it; this task pins header == COLUMNS), `docs/results.md` (header regenerated once by the implementer via `python -m evals.publish --regenerate-header`; the policy text gains the "how a row gets here" paragraph), `README.md` (Results section: link + framing), `.claude/rules/evals.md` (append-only is enforced by `append_result_row`)

## Interfaces

```python
# evals/publish.py
RESULTS_PATH = Path("docs/results.md")
def render_row(row: ResultRow, *, date: date, git_sha: str, models: str) -> str        # "| 2026-09-20 | 1a2b3c4 | triage-v4 | gpt-4o-mini→gpt-5.4 | 214 | 0 | 0.71 | 0.93 | … |" in COLUMNS order; Decimals as "0.000412"; floats with 2–3 dp per column spec
def append_result_row(path: Path, line: str) -> None                                     # appends after the last table row; raises ValueError if the header row != render_header() (column drift) or the file lacks the "## Runs" section; NEVER rewrites existing lines
def render_header() -> str                                                               # from evals.scoring.COLUMNS
def regenerate_header(path: Path) -> None                                                # only replaces the header + separator lines (used once by task-04/06 when COLUMNS grows); existing rows untouched
async def write_eval_run_row(session_factory, *, git_sha, prompt_version, model_config, started_at, metrics) -> uuid.UUID
# evals/run.py: --publish (default off) → for each row: append_result_row(...); with --database-url also write_eval_run_row; prints "published <n> row(s) to docs/results.md" ; refuses --publish for a non-v2 golden file (v1 numbers are never published — `.claude/rules/evals.md`)
```

## Controller rulings carried into this task (2026-09-17 — binding, exact)

- **R35 (task-04) already created `evals/publish.py`** with `metrics_payload(metrics) -> dict[str, Any]` and `async def write_eval_run_row(session: AsyncSession, *, git_sha, prompt_version, model_config, started_at, metrics) -> uuid.UUID` (session-first, `flush()` only), and `evals.run --database-url` (+ `--schema`) already writes the `eval_runs` row per prompt version. This task does NOT redefine them: the `write_eval_run_row(session_factory, …)` line in Interfaces above is superseded — reuse task-04's function and its `--database-url` seam; `--publish` adds the `docs/results.md` append and, when `--database-url` is present, the row write is the one task-04 already does (do not write it twice; the "DB-less runs skip the row with a notice" sentence stands). The `scripts/check_real_sessions.py` seam mentioned above is NOT used.
- **R41 — `--from-artifact PATH`** (spine task-07 needs it: publishing a row from a nightly run's uploaded JSON without re-running the LLM). `evals/publish.py::row_from_artifact(path: Path) -> PublishedRun` where `@dataclass(frozen=True) class PublishedRun: row: ResultRow; date: date; git_sha: str; models: str` is rebuilt from the per-run JSON payload (`prompt_version`, `model`, `git_sha`, `started_at`, `metrics` via task-05's `evals/publish.py::metrics_from_payload` (ruling R43 — created there for `load_baseline`; this task REUSES it). `evals.run --publish --from-artifact PATH` publishes exactly that one row and makes NO LLM call (`--golden`/`--prompt` are ignored with it; the v2-only refusal reads the artifact's `golden` field — task-04's payload gains a `"golden": str(args.golden)` key if it lacks one; the implementer adds it here with a test). Test: `test_publish.py::test_metrics_from_payload_round_trips` (`metrics_from_payload(metrics_payload(m)) == m` for a judged `score()` result) and `tests/test_evals_run.py::test_publish_from_artifact_appends_without_llm_calls` (FakeLLMClient records zero calls).
- **`models` cell:** `"<model>"` for an un-escalated run and `"<model>→<strong>"` when `--strong-model` was given (the arrow is U+2192); `render_row`'s `models` argument is computed by `evals.run` from its effective config.
- **Header regeneration:** task-04 already regenerated `docs/results.md`'s header once for the 23-column `COLUMNS`; `regenerate_header` still lands here as the tool for future growth, and a test pins header == `render_header()`.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| render | `test_publish.py::test_render_row_follows_columns_order_and_formats` | column count == len(COLUMNS); Decimal/float formatting; the models cell |
| append-only | `::test_append_result_row_only_appends` | file before/after: every prior line byte-identical; exactly one new line; a second append lands after it |
| header drift | `::test_append_refuses_on_header_drift` | a stale header → `ValueError`, file untouched |
| regenerate | `::test_regenerate_header_keeps_rows` | rows preserved byte-for-byte, header replaced |
| v1 refused | `tests/test_evals_run.py` (extend) `::test_publish_refuses_v1` | `--publish` on `v1.jsonl` → exit 1 `config_error` |
| eval_runs row | `::test_publish_writes_eval_runs_row` (DB fixtures) | one `EvalRunRow` with `metrics` JSON == `_metrics_payload` |
| README | `tests/test_results_doc.py` (extend, unpinned) `::test_readme_links_results_and_results_has_a_real_row` | README links `docs/results.md`; at the gate: ≥ 3 rows, ≥ 2 distinct prompt versions |

## Steps (TDD)

- [ ] Steps 1–2 (test-author): RED; commit `test(evals): results publication append-only + eval_runs row RED (m7 task-06)`.
- [ ] Steps 3–4 (implementer): `publish.py`, run flags, header regeneration, README; full gates; commit `feat(evals): publish results rows append-only; eval_runs row; README results link (m7 task-06)`.
- [ ] **Step 5 — controller (live):** three runs with `--publish` (`triage-v4`, `triage-v3` same sha; `triage-v4` again the next day); commit each `data(results): …`; the numbers are whatever they are (worse rows stay).

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_publish.py tests/test_evals_run.py tests/test_results_doc.py   # all pass, 0 skipped
git log --oneline -- docs/results.md | head -3                                                   # only append commits since the header regeneration
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- Every published row is written by the harness, carries all §7.3 columns, and can only be appended; the README links a table with three real v2 rows across two prompt versions.
