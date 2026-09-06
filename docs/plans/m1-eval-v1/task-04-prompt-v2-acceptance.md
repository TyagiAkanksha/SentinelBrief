---
id: task-04
milestone: m1-eval-v1
depends_on: [task-03]
status: planned
spec: PRD.md §12 M1 (two comparable rows), §7.5 (results table shape), §6.5/§6.6; CONVENTIONS.md §13
---

# task-04 — Prompt `triage-v2`, prompt-contract tests, `docs/results.md` stub, M1 acceptance + tag

## Goal

A second, immutable prompt version exists (`triage-v2.md`) that differs from v1 in a defensible
way; v1's hash pin still passes; the parametrized prompt-contract test covers both; a
`docs/results.md` stub carries the publication policy and an empty table whose header matches
`format_table`; the PRD §12 M1 clause — two prompt versions → two comparable rows — is
demonstrated live and `m1` is tagged.

## Context (read ONLY these)

- `PRD.md` §12 M1, §7.5, §6.5, §6.6.
- `CONVENTIONS.md` §13 · `.claude/skills/new-prompt-version/SKILL.md` (follow it).
- `worker/prompts/triage-v1.md`, `tests/test_prompts.py` (M0 task-04); `evals/run.py` (task-03).

## Files

- Create: `worker/prompts/triage-v2.md`, `tests/test_evals_live.py`
- Modify: `docs/results.md` (exists as a policy stub; add the empty table with the fixed header),
  `tests/test_prompts.py` (add `test_shipped_v2_hash_pinned` — implementer, Step 4),
  `README.md` ("Evaluation" section de-stubbed)

## Interfaces

- **Consumes:** `load_prompt`, `PROMPTS_DIR`, `evals.run.main`, `COLUMNS`.
- **Produces (later tasks rely on — produce exactly):**
  - `worker/prompts/triage-v2.md` = v1 plus (a) an explicit instruction to cite at least two
    concrete evidence items from the data block in `reasoning`, and (b) a three-step decision
    order: severity first, then escalate, then category. Same placeholder, markers and rubric.
    Top comment: "v2 — adds evidence-citation and decision-order instructions; v1 untouched."
  - `docs/results.md`: keep the existing policy section; replace the "Runs" placeholder with an
    empty markdown table whose header is exactly `date, git_sha, prompt_version, models` +
    `COLUMNS[2:]`, followed by the line "No published runs yet — the first appears at M7."
  - `tests/test_evals_live.py::test_live_two_prompt_versions_produce_two_rows`
    (`@pytest.mark.live`; runs `main(["--golden", "evals/golden/v1.jsonl", "--prompt",
    "triage-v1", "--prompt", "triage-v2", "--output-dir", str(tmp_path)])`, asserts exit 0 and two
    body rows; skips when the key is empty).

## Steps (TDD)

- [ ] **Step 1: Write failing tests:** `tests/test_prompts.py` gains
  `test_v2_loads_and_differs_from_v1` (both load; texts differ; v2 contains "at least two") and
  the existing parametrized contract test must now see two files; `tests/test_evals_live.py` as
  above.
- [ ] **Step 2: Run to see them fail** → Expected: `ConfigError: prompt triage-v2 not found`.
- [ ] **Step 3: Write `triage-v2.md`** by copying v1 and editing only the new file.
- [ ] **Step 4: Add `test_shipped_v2_hash_pinned`** (implementer-added test) and confirm
  `test_shipped_v1_hash_pinned` still passes (v1 untouched).
- [ ] **Step 5: Write `docs/results.md` stub; README "Evaluation" section.**
- [ ] **Step 6: Full gates → commit:**
  `feat(worker): triage-v2 prompt; results.md stub; prompt-contract tests (m1 task-04)`.
- [ ] **Step 7: Acceptance walk** (`/milestone-gate m1`): with the key exported, run
  `uv run python -m evals.run --golden evals/golden/v1.jsonl --prompt triage-v1 --prompt triage-v2`
  and paste the two-row table into the **ledger only**; run `uv run pytest -q -m live` and
  paste.
- [ ] **Step 8: Whole-branch review → fix wave → PR → merge → tag `m1`;** write the M2 briefs (already
  present in `docs/plans/m2-service-persistence/` — re-read them against what M0/M1 actually
  produced and amend names if anything drifted).

## Verify

```bash
uv run pytest -q tests/test_prompts.py                                                     # all passed (v1 + v2 pins, contract x2)
sha256sum worker/prompts/triage-v1.md                                                      # unchanged since M0
uv run python -m evals.run --golden evals/golden/v1.jsonl --prompt triage-v1 --prompt triage-v2   # two rows, same columns
git tag --list 'm*'                                                                        # m0 m1 (after merge)
```

## Acceptance

- PRD §12 M1: two different prompt versions produce two comparable rows (live, pasted in the
  ledger).
- v1 byte-identical to its M0 pin; v2 pinned; `docs/results.md` exists with the policy and an
  empty, correctly-headed table.
