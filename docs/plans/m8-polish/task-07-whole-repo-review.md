---
id: task-07
milestone: m8-polish
depends_on: [task-06]
status: planned
spec: PRD.md §12 M8 (final polish), §14 (definition of done); `docs/plans/m8-polish.md` Global Constraints ("the whole-repo review runs on the strongest model with the complete Minors ledger from every milestone; its remediation batch is planned as `docs/plans/m8r-*.md` only if the owner opts in")
---

# task-07 — Whole-repo review on the strongest model, with the complete cross-milestone Minors ledger

## Goal
The closing review: one pass over the WHOLE repository (not a single branch's diff) on the
strongest model, holding the complete deferred/Minors ledger accumulated across M0–M8 — every
"defer to M8b/M9/accept" from every milestone's whole-branch review. It verifies the §14 definition
of done end to end (live URL serving, CI green including the nightly gate, ≥3 published eval runs
across ≥2 prompts, resume/placeholder text replaced), re-checks the security & cost invariants
holistically (LLM only in worker+evals; no public path computes; retriage gated+capped; token
budget enforced; no secret/label/payload committed; import-linter contracts), and triages the full
Minors ledger: fix-now (a final small wave), accept, or — only if the owner opts in — a remediation
plan `docs/plans/m8r-*.md`. It produces the definition-of-done report that closes the project.

## Context (read ONLY these)
- `PRD.md` §12, §14. `docs/plans/m8-polish.md`. Every milestone's archived final review + deferred
  list under `.superpowers/sdd/_archive/*/` and `.superpowers/sdd/*/`, especially
  `.superpowers/sdd/m7-eval-hardening/whole-branch-deferred.md` (the M8b-carried set) and the M8a
  deferred Minors. `SUGGESTIONS.md` (out-of-scope ideas stay there, never pulled into scope).
- The live production instance (app host `i-0227c9985795b0a55`), the nightly workflow runs, the
  published `docs/results.md`.

## Interfaces
No code interfaces. Output is a report `.superpowers/sdd/m8-polish/m8b-whole-repo-review.md` (or via
the reviewer's Bash heredoc if it lacks Write) with: the §14 walk (each clause → evidence), the
holistic invariant checks, the full Minors triage table, and a sign-off. If findings warrant a
final fix wave, ONE fix dispatch + one scoped re-review (the milestone-gate pattern).

## Assemble before dispatch (controller)
- A single `m8b-deferred-ledger.md` collecting EVERY still-open Minor from M0–M8 (grep the archived
  ledgers + the M7/M8a deferred files), each with its origin, file:line, and prior triage — the
  reviewer's triage lens.
- The whole-repo review range is the repository at HEAD (post all merges), not a diff; the reviewer
  reads the tree, runs the full gates cold, and mutation-tests the highest-value invariants across
  packages.

## Steps
- controller: assemble the cross-milestone ledger; run the full gates once to confirm green;
  dispatch the whole-repo reviewer on the strongest model with the ledger + the §14 walk checklist.
- If clean → the definition-of-done report closes M8b; PR `feat/m8b-polish` → main → tag `m8`.
- If findings → one fix wave + one scoped re-review, then tag.
- Owner opt-in only: a `docs/plans/m8r-*.md` remediation plan for anything deferred beyond M8.

## Verify
Full gates green at HEAD; the §14 clauses each demonstrated with pasted evidence (live curl, CI run
links, the ≥3 published rows, the nightly gate run); the review report signed off.

## Acceptance
A whole-repo review on the strongest model with the complete Minors ledger; the §14 definition of
done demonstrated; M8b (and the project's Phase-1 scope) closed and tagged `m8`.
