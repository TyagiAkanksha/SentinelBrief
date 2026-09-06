---
id: task-05
milestone: m0-core-loop
depends_on: [task-04]
status: planned
spec: PRD.md §12 M0 (the acceptance clauses); CONVENTIONS.md §5 (entrypoints), §10 (live marker)
---

# task-05 — `worker.triage_one` CLI, live smoke, M0 acceptance walk + tag

## Goal

`uv run python -m worker.triage_one fixtures/alerts/alert1.json` prints a validated verdict as
JSON with one line of accounting, exits 0; validation failure after the retry exits 2 with one
stderr line and no traceback; config/LLM/file errors exit 1; all five fixtures triage with the
fake and (once, with the owner's key) live. The PRD §12 M0 clauses are demonstrated and `m0` is
tagged.

## Context (read ONLY these)

- `PRD.md` §12 M0.
- `CONVENTIONS.md` §5 (nothing imports `worker.main`; the CLI is an entrypoint), §10.
- `worker/triage.py`, `worker/llm_client.py`, `core/config.py` (tasks 02–04).
- `.claude/skills/milestone-gate/SKILL.md` — the gate procedure this task ends with.

## Files

- Create: `worker/triage_one.py`, `tests/test_triage_one.py`, `tests/test_triage_live.py`
- Modify: `README.md` — de-stub "2. Run the core loop"; remove "(from M0)" markers.

## Interfaces

- **Consumes:** `TriagePipeline`, `TriageOutcome`, `OpenAICompatibleLLMClient.from_settings`,
  `Settings`, `SessionAlert`, `ConfigError`, `LLMCallError`, `VerdictValidationError`,
  `FakeLLMClient`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # worker/triage_one.py
  def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int: ...
  if __name__ == "__main__":
      raise SystemExit(main())
  ```

  CLI: `python -m worker.triage_one <alert.json> [--model MODEL] [--prompt VERSION]`;
  `--model` defaults to `Settings().cheap_model`, `--prompt` to `Settings().triage_prompt_version`;
  `llm=None` → `OpenAICompatibleLLMClient.from_settings(settings)`.
  stdout: one JSON document
  `{"verdict": {...}, "model": "...", "prompt_version": "...", "input_tokens": n, "output_tokens": n, "cost_usd": "0.000123", "latency_ms": n, "retried": false}`
  (`json.dumps(..., indent=2)`).
  Exit codes: `0` success; `1` usage error, unreadable/invalid alert file, `ConfigError`,
  `LLMCallError` (one line on stderr: `error: <code>: <message>`); `2` `VerdictValidationError`
  (one line on stderr: `error: verdict_validation: failed after 2 attempts: <last_error>`; no
  traceback).

## Steps (TDD)

- [ ] **Step 1: Write failing tests** in `tests/test_triage_one.py` (drive `main([...], llm=fake)`
  with `capsys`; env via `monkeypatch.setenv("CHEAP_MODEL", "fake-model")` and
  `MODEL_PRICES_JSON`): `test_prints_verdict_json_exit_zero` (stdout parses; `verdict.severity`
  in 1..5), `test_runs_all_five_fixtures` (parametrized; a fresh fake per fixture),
  `test_exit_2_on_verdict_validation_error_no_traceback` (`["{}", "{}"]`; `"Traceback" not in
  err`), `test_exit_1_on_missing_file`, `test_exit_1_on_llm_call_error`,
  `test_exit_1_on_config_error` (`CHEAP_MODEL` empty and `llm=None`).
  `tests/test_triage_live.py::test_live_triage_all_fixtures` (`@pytest.mark.live`; loops the five
  fixtures with the real client; asserts each exit 0; skips when the key is empty).
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: worker.triage_one`.
- [ ] **Step 3: Implement `worker/triage_one.py`** with `argparse`; errors mapped to exit codes;
  `asyncio.run(pipeline.run(alert))`.
- [ ] **Step 4: Run the tests → pass; `uv run mypy` clean.**
- [ ] **Step 5: README** — real commands for the core loop.
- [ ] **Step 6: Full gates → commit:**
  `feat(worker): triage_one CLI with exit-code contract (m0 task-05)`.
- [ ] **Step 7: Acceptance walk** (`/milestone-gate m0`): with the owner's key exported
  (`LLM_API_KEY`, `CHEAP_MODEL`, `MODEL_PRICES_JSON`), run the CLI on each of the five fixtures
  and paste the five JSON outputs into the ledger; run `uv run pytest -q -m live` and paste; run
  the CLI once with `--prompt does-not-exist` → exit 1 line pasted; force the validation path by
  running `main(["fixtures/alerts/alert1.json"], llm=FakeLLMClient(["{}", "{}"]))` from
  `uv run python -c` → exit 2 line pasted.
- [ ] **Step 8: Whole-branch review → fix wave → PR `feat/m0-core-loop` → `main` → tag `m0`.**

## Verify

```bash
uv run python -m worker.triage_one fixtures/alerts/alert1.json            # JSON verdict, exit 0 (key exported)
uv run python -m worker.triage_one fixtures/alerts/nope.json; echo $?     # "error: ..." then 1
uv run pytest -q                                                          # all passed, 0 skipped (live excluded by addopts)
uv run pytest -q -m live                                                  # 2 passed with the key, else 2 skipped
git tag --list 'm*'                                                       # m0 (after merge)
```

## Acceptance

- PRD §12 M0: the CLI prints a validated `Verdict` on `alert1.json`; runs on all 5 fixtures;
  validation failures retry once then error cleanly (exit 2, one line).
- Gates and CI green on the PR; `m0` tagged on the merge commit; ledger holds the pasted
  evidence; the M1 briefs are already in `docs/plans/m1-eval-v1/`.
