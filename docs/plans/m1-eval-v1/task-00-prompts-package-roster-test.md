---
id: task-00
milestone: m1-eval-v1
depends_on: []
status: planned
spec: CONVENTIONS.md §2 (layout), §7 (env roster test), §13 (prompts); M0 final review plan defects 4 and 5
---

# task-00 — `worker/prompts` becomes a package; env-roster test

## Goal

The M0 layout shipped `worker/prompts.py` (loader module) next to `worker/prompts/` (markdown
directory), which works only because CPython prefers a module over a namespace directory. Before
`evals.run` (task-03) and the M2 Dockerfile depend on it, the loader moves to
`worker/prompts/__init__.py` so `worker.prompts` is a regular package that contains its own
prompt files. Nothing else changes: import path, `PROMPTS_DIR` semantics, markdown paths and the
v1 hash pin stay identical. Second, the env-roster test that `CONVENTIONS.md` §7 and
`.claude/rules/core.md` already cite is created now (M1 adds no `Settings` field, so it is green
on arrival).

## Context (read ONLY these)

- `CONVENTIONS.md` §2 (the layout line for `worker/prompts`), §7, §13.
- `worker/prompts.py` (M0 task-04), `tests/test_prompts.py`, `tests/test_prompt_pins.py`.
- `.superpowers/sdd/m0-core-loop/m0-final-review.md` — "Plan defects" items 4 and 5.

## Files

- Move: `worker/prompts.py` → `worker/prompts/__init__.py` (`git mv`; body unchanged except
  `PROMPTS_DIR`)
- Create: `tests/test_env_example_roster.py`
- Modify: `CONVENTIONS.md` §2 layout block (`prompts.py` line → `prompts/__init__.py  # loader:
  load_prompt / build_messages; triage-vN.md files live beside it`)

## Interfaces

- **Consumes:** `worker.prompts` public names (`PROMPTS_DIR`, `SCHEMA_PLACEHOLDER`,
  `ALERT_DATA_BEGIN`, `ALERT_DATA_END`, `load_prompt`, `build_messages`), `core.config.Settings`.
- **Produces (later tasks rely on — produce exactly):**
  - `worker/prompts/__init__.py` with the identical public API; `PROMPTS_DIR: Path =
    Path(__file__).parent` (was `.parent / "prompts"`); `load_prompt("triage-v1")` still reads
    `worker/prompts/triage-v1.md`.
  - `tests/test_env_example_roster.py::test_every_settings_field_documented_in_env_example` —
    for every `name in Settings.model_fields`, `.env.example` contains a line matching
    `^#? ?{NAME}=` (`NAME = name.upper()`); failure message lists the missing names.
  - `tests/test_env_example_roster.py::test_env_example_has_no_unknown_settings_lines` — every
    uncommented `NAME=` line in `.env.example` whose `NAME` is not a `Settings` field is one of
    the documented non-Settings variables (`REDIS_URL`, `NEXT_PUBLIC_API_URL`, `API_URL`,
    `TEST_DATABASE_URL`, and the M2+/M4+/M8+ names listed in the test as `_SCHEDULED`), so a
    typo'd variable cannot hide.

## Steps (TDD)

- [ ] **Step 1 (test-author): write the two roster tests** and a regression pin for the package
  move in `tests/test_prompts_package.py`: `test_worker_prompts_is_a_regular_package`
  (`Path(worker.prompts.__file__).name == "__init__.py"`) and
  `test_prompts_dir_contains_v1` (`(PROMPTS_DIR / "triage-v1.md").is_file()`). Interfaces → test
  table in the report. Run: the roster tests pass already (green-on-arrival is expected and
  stated); the package test FAILS (`prompts.py`).
- [ ] **Step 2 (implementer): `git mv worker/prompts.py worker/prompts/__init__.py`;** set
  `PROMPTS_DIR = Path(__file__).parent`; update the `CONVENTIONS.md` §2 line.
- [ ] **Step 3: full gates cold** → all green; `tests/test_prompt_pins.py` unchanged and passing
  (the markdown path did not move); `uv run python -m worker.triage_one fixtures/alerts/alert1.json
  --prompt does-not-exist` still exits 1 with the `config_error` line.
- [ ] **Step 4: commit** `refactor(worker): prompts loader becomes the prompts package; env-roster
  test (m1 task-00)`.

## Verify

```bash
uv run python -c "import worker.prompts as p; print(p.__file__, p.PROMPTS_DIR)"   # .../worker/prompts/__init__.py .../worker/prompts
uv run pytest -q tests/test_prompts.py tests/test_prompt_pins.py tests/test_prompts_package.py tests/test_env_example_roster.py
uv run ruff check --no-cache . && uv run mypy --no-incremental && uv run lint-imports
```

## Acceptance

- `worker.prompts` resolves to `worker/prompts/__init__.py`; no test outside the new file changed;
  the v1 hash pin still passes.
- The roster tests exist and are green; adding an undocumented `Settings` field makes the first
  one fail (ritual: add a dummy field in a scratch worktree, run, discard).
