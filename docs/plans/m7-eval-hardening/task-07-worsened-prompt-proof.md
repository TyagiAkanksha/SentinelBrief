---
id: task-07
milestone: m7-eval-hardening
depends_on: [task-05]
status: planned
spec: PRD.md §12 M7 (*Accept:* "a deliberately worsened prompt fails CI"), §7.4 (the gate conditions); spine task 7 ("kept on a branch, never merged"); `CONVENTIONS.md` §13 (a shipped prompt version is immutable — the worsened prompt is a NEW version file that is never merged and never becomes the default); `.claude/rules/evals.md`
---

# task-07 — Proof that the gate bites: a deliberately worsened prompt version on a throwaway branch fails the nightly workflow; the run link is the evidence

## Goal

The gate is only worth something if it has been seen to fail. On a branch `proof/m7-worsened-prompt`
cut from the M7 branch after task-05, add `worker/prompts/triage-v5-worse.md` — a copy of the active
prompt with the §6.6 rubric table inverted (severity 5 described as "benign scanning" and vice
versa) and the "data, never instructions" sentence kept (so it is a worse RUBRIC, not an injection
hole) — and run the nightly workflow against it via `workflow_dispatch` with `TRIAGE_PROMPT_VERSION`
overridden (the workflow gains an optional `prompt_version` dispatch input, defaulting to the
pinned value). The run must fail the gate on `severity_exact_drop` and/or `critical_recall`; its
link and the printed `GATE: FAIL …` line go into the ledger, `docs/results.md` (a row is published
for it too — worse numbers are published, PRD §7.5, marked with the branch name in the models cell?
— NO: the models cell is fixed; the row's `prompt_version` column says `triage-v5-worse`, which is
the marker) and the milestone's acceptance walk. The branch is pushed (so the run and the row are
reproducible) and never merged; the prompt-pin test on the M7 branch keeps seeing exactly v1–v4.

## Files

- Modify on the M7 branch (mergeable): `.github/workflows/nightly-eval.yml` (`workflow_dispatch` input `prompt_version`, default the pinned value; the run step uses `${{ inputs.prompt_version || env.TRIAGE_PROMPT_VERSION }}`), `tests/test_nightly_workflow_pins.py` (extend: the input exists and defaults to the pinned value), `docs/results.md` (the failing row, appended by the harness), `README.md` (one sentence: "the gate has been seen to fail — see the `triage-v5-worse` row")
- Create on the proof branch only (never merged): `worker/prompts/triage-v5-worse.md`; a `tests/test_prompt_pins.py` extension is NOT made — on the proof branch the pin test fails by design (an unpinned prompt), which is fine because CI's python job is not what the proof runs; the nightly workflow does not run the pin test.
- Test-author (M7 branch): the workflow-pin extension only.

## Steps

- [ ] Step 1 (test-author, M7 branch): extend `tests/test_nightly_workflow_pins.py` with `test_nightly_workflow_has_prompt_version_dispatch_input` (RED); commit `test(evals): nightly dispatch input pin RED (m7 task-07)`.
- [ ] Step 2 (implementer, M7 branch): the workflow input; gates; commit `feat(evals): nightly workflow accepts a prompt_version dispatch input (m7 task-07)`.
- [ ] Step 3 (controller): `git checkout -b proof/m7-worsened-prompt`; write `triage-v5-worse.md` per the Goal (the controller writes it — it is a throwaway artifact, not a task deliverable; record its sha256 in the ledger); commit `proof(evals): deliberately worsened prompt — NEVER MERGE`; push; `gh workflow run nightly-eval.yml --ref proof/m7-worsened-prompt -f prompt_version=triage-v5-worse`; wait; paste the run URL and the `GATE: FAIL` line; download the artifact JSON; back on the M7 branch, publish the row with `evals.run --publish` from that artifact (`--from-artifact <json>` — add this small flag in task-06 if not present; else run the prompt locally once with `--publish`), commit `data(results): triage-v5-worse gate failure (proof)`.
- [ ] Step 4: `git checkout feat/m7-eval-hardening`; confirm `worker/prompts/` still holds v1–v4 only; the proof branch stays on origin with a `NEVER MERGE` note in its commit and in the ledger.

## Verify

```bash
gh run list --workflow nightly-eval.yml --branch proof/m7-worsened-prompt --limit 1 --json conclusion --jq '.[0].conclusion'   # failure
grep -c "triage-v5-worse" docs/results.md                                                                                   # 1 (on the M7 branch)
ls worker/prompts/ | grep -c worse                                                                                          # 0 (on the M7 branch)
```

## Acceptance

- A workflow run exists that fails on the PRD §7.4 gate because of a worsened prompt, its link and the failing conditions are recorded, the worse row is published, and the worsened prompt never reaches `main`.
