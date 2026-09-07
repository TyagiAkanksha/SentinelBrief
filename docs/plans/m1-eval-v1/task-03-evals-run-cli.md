---
id: task-03
milestone: m1-eval-v1
depends_on: [task-02]
status: planned
spec: PRD.md §7.2 (CLI), §7.3, §12 M1; CONVENTIONS.md §5 (entrypoints), §10
---

# task-03 — `evals.run` CLI: `run_golden`, repeatable `--prompt`, injectable LLM, result JSON

## Goal

`uv run python -m evals.run --golden evals/golden/v1.jsonl --prompt triage-v1 [--prompt
triage-v2] [--model ID] [--concurrency 4] [--output-dir evals/results]` drives the **real**
`TriagePipeline` over every golden case (one pipeline per prompt version), captures failures as
`CaseResult.error`, prints one table row per prompt, and writes one JSON per prompt under the
gitignored `evals/results/`.

## Context (read ONLY these)

- `PRD.md` §7.2 (flags; env + flags only), §7.3, §12 M1.
- `evals/golden.py`, `evals/scoring.py` (tasks 01–02); `worker/triage.py`,
  `worker/llm_client.py`, `core/config.py` (M0).
- `.claude/rules/evals.md` (real pipeline, never a reimplementation; v1 never published).

## Files

- Create: `evals/run.py`, `evals/results/.gitkeep`, `tests/test_evals_run.py`

## Interfaces

- **Consumes:** `GoldenCase`, `load_golden`, `CaseResult`, `RunMetrics`, `ResultRow`, `score`,
  `format_table`, `TriagePipeline`, `TriageOutcome`, `OpenAICompatibleLLMClient.from_settings`,
  `Settings`, `VerdictValidationError`, `LLMCallError`, `FakeLLMClient`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # evals/run.py
  async def run_golden(cases: Sequence[GoldenCase], *, pipeline: TriagePipeline, concurrency: int = 4) -> list[CaseResult]: ...
      # asyncio.Semaphore(concurrency); results in input order; VerdictValidationError | LLMCallError -> CaseResult(verdict=None, error=f"{e.code}: {e}")
      # (tokens/cost for a failed case are unknown to the pipeline in M0 -> recorded as 0; M5's routing task carries partial usage through)
  def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int: ...
      # parses flags; --prompt is action="append" (required, ≥1); --model default Settings().cheap_model;
      # llm=None -> OpenAICompatibleLLMClient.from_settings; for each prompt: pipeline -> run_golden -> score -> ResultRow;
      # prints format_table(rows); writes evals/results/<YYYYMMDDTHHMMSSZ>-<prompt>.json =
      #   {"prompt_version", "model", "git_sha", "started_at", "metrics": asdict(RunMetrics), "cases": [asdict(CaseResult) with verdict as dict]}
      # exit 0; every failure path below is exit 1 with one stderr line
  if __name__ == "__main__": raise SystemExit(main())
  ```

  **Failure paths — enumerated (M0 plan defect 3); every one is exit `1`, one stderr line
  `error: <code>: <message>`, empty stdout, never a traceback:**

  | Path | code |
  |---|---|
  | argparse usage error (no `--prompt`, unknown flag) — parser subclass, same shape as `worker/triage_one.py` | `usage` |
  | `Settings()` fails validation (e.g. malformed `MODEL_PRICES_JSON`) | `config_error` |
  | golden file missing / unreadable / invalid row (`load_golden` raises) | `invalid_golden` |
  | `ConfigError` from `from_settings` (empty/unpriced model) or from `TriagePipeline(...)` (unknown `--prompt`) — raised before any case runs | `config_error` |
  | output directory not writable | `output_error` |

  Per-case `VerdictValidationError` / `LLMCallError` are **not** failures of the run: they become
  `CaseResult.error` and the run exits `0` — unless every case failed, then exit `1` with code
  `all_cases_failed`. **Price before spend** (M0 defect 2): the pipeline's client resolves the
  price before each call, so an unpriced `--model` fails at construction, never mid-run;
  `evals.run` reports the pipeline's accounted cost and never estimates.

## Steps (TDD)

- [ ] **Step 1: Write failing tests** in `tests/test_evals_run.py` (small golden fixtures built in
  the test via `SessionAlert` + `GoldenLabel`, `FakeLLMClient` replies):
  `test_run_golden_one_result_per_case_in_order`,
  `test_run_golden_captures_pipeline_failure_as_error` (`["{}", "{}"]` for one case → its
  `error` starts with `verdict_validation`), `test_main_prints_one_row_per_prompt_version`
  (`--prompt triage-v1 --prompt triage-v1`, fake with enough replies → two body lines),
  `test_main_passes_prompt_versions_to_pipeline` (with a second prompt file created in `tmp_path`
  and `PROMPTS_DIR` monkeypatched → the fake's recorded system messages differ),
  `test_main_writes_result_json` (`--output-dir tmp_path` → one file per prompt, keys present),
  `test_main_exit_1_on_missing_golden_file`, and one test per remaining failure-path row:
  `test_main_usage_error_exit_1` (no `--prompt`), `test_main_exit_1_on_malformed_settings`
  (`MODEL_PRICES_JSON=not-json`), `test_main_exit_1_on_invalid_golden_row`,
  `test_main_exit_1_on_unknown_prompt_before_any_case` (fake `calls` stays empty),
  `test_main_exit_1_when_all_cases_failed` (code `all_cases_failed`). The report carries the
  Interfaces → test table.
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: evals.run`.
- [ ] **Step 3: Implement `evals/run.py`.** Docstring cites PRD §7.2 and the v1-never-published
  rule.
- [ ] **Step 4: Tests pass; `uv run mypy` clean; `uv run lint-imports` still 5 kept** (`evals`
  importing `worker` is allowed).
- [ ] **Step 5: Full gates → commit:** `feat(evals): evals.run CLI over the real pipeline (m1 task-03)`.

## Verify

```bash
uv run pytest -q tests/test_evals_run.py                                             # 6 passed
uv run python -m evals.run --golden evals/golden/v1.jsonl --prompt triage-v1        # one-row table (key exported); JSON under evals/results/
git status --short evals/results                                                    # nothing to commit (gitignored)
```

## Acceptance

- One invocation with N `--prompt` flags prints N comparable rows with identical columns.
- Failures never abort a run; they appear as `error` and count against the metrics.
- Result JSON is written per prompt and stays out of git.
