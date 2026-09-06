---
name: milestone-gate
description: Walk a SentinelBrief milestone through its gate — ledger complete, full gates, whole-branch review, fix wave, PRD §12 acceptance walk with pasted output, PR into main, tag mN, user checkpoint, then brief the next milestone. Invoke as /milestone-gate m0 (etc.). User-invoked only; it creates tags and PRs.
disable-model-invocation: true
---

# Milestone gate — `$ARGUMENTS`

Runs the gate procedure from `docs/plans/README.md` for milestone `$ARGUMENTS` (e.g. `m0`). Every
step produces evidence that goes into the ledger (`.superpowers/sdd/<spine-basename>/progress.md`).
Do not skip a step because "it obviously passes".

## 0. Preconditions

- [ ] The ledger's first line names `docs/plans/$ARGUMENTS-*.md`, and every task in the spine's
      task table has a `Task N: complete` line. If not, stop: the milestone is not finished.
- [ ] `git status --short` is clean on branch `feat/$ARGUMENTS-<slug>`.

## 1. Full gates, cold cache

- [ ] Run `/gates` with the export line, using the cold-cache variant. Paste the output into the
      ledger.

## 2. Whole-branch review

- [ ] Build the review package (`superpowers:subagent-driven-development`'s `scripts/review-package
      <spine> <base-sha> <head-sha>`, base = the merge-base with `main`).
- [ ] Dispatch the `reviewer` agent on the **strongest model available** with: the spine, every task
      brief, every task report, the full Minors ledger, and the package. Ask for a whole-branch
      verdict plus a triage of ledgered Minors into fix-before-merge / defer-with-owner.
- [ ] Persist findings verbatim in the ledger.

## 3. Fix wave

- [ ] One `implementer` dispatch with the full fix list (Criticals, Importants, and the
      fix-before-merge Minors). Then a confirm pass by the same reviewer. Repeat until Approved.

## 4. PRD §12 acceptance walk

- [ ] Open `PRD.md` §12 for `$ARGUMENTS`. For **each** *Accept* clause, run the command that
      demonstrates it and paste the real output into the ledger under `Acceptance:`. Clauses that
      need a live LLM key are run with the key exported and the output pasted (never the key).
- [ ] If any clause cannot be demonstrated, stop and report — the gate fails.

## 5. Merge and tag

- [ ] Push the branch; open a PR into `main` titled `$ARGUMENTS: <milestone name>` whose body lists
      the acceptance clauses with a one-line evidence pointer each, ending with the 🤖 footer from
      the session's attribution rules.
- [ ] After the owner merges: `git tag -a $ARGUMENTS -m "<milestone name>" <merge-commit>` and
      `git push origin $ARGUMENTS`. The SessionStart hook reports this tag from now on.

## 6. User checkpoint

- [ ] Hand the owner: what shipped, the acceptance evidence, deferred Minors with owners, and any
      PRD ambiguity that surfaced. Wait for their go.

## 7. Brief the next milestone

- [ ] Use `superpowers:writing-plans` to write the task briefs for the next spine
      (`docs/plans/m<N+1>-*.md` → `m<N+1>-*/task-NN-*.md`) in the AdvisorDesk brief format
      (frontmatter, Goal, Context, Files, Interfaces, Steps, Verify, Acceptance). Commit them as
      `docs(plans): m<N+1> task briefs`. Update the status column in `docs/plans/README.md`.
