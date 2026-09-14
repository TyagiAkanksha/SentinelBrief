# SentinelBrief — Implementation Plans Registry

This directory holds the build plan for the SentinelBrief PRD
([`../../PRD.md`](../../PRD.md), v1.1 — authoritative on any conflict).

One shape only: **a spine file per PRD §12 milestone** (`mN-<slug>.md`: goal, global constraints,
task table, ordering rationale, acceptance walk) with **one `task-NN-<slug>.md` brief per task**
inside the sibling folder `mN-<slug>/`. Subtasks are the checkboxed steps *inside* each brief —
deliberately not separate files, so one agent gets a whole task in a single read.

Why the spine is a flat file rather than `mN-<slug>/00-INDEX.md`: superpowers 6.3.0's
`subagent-driven-development` keys each plan's ledger workspace on the plan file's **basename**
(`.superpowers/sdd/<basename>/`). Nine `00-INDEX.md` files would share one ledger; nine
`mN-<slug>.md` files each get their own.

**Conventions cited by every brief:** [`../../CONVENTIONS.md`](../../CONVENTIONS.md) (Python) ·
[`../FRONTEND-CONVENTIONS.md`](../FRONTEND-CONVENTIONS.md) (`web/`) ·
[`../../CLAUDE.md`](../../CLAUDE.md) (working rules).

## Required plugins/skills

Implementation runs on the **superpowers** plugin (6.3.0 installed here; on another machine:
`/plugin marketplace add obra/superpowers-marketplace` → `/plugin install
superpowers@superpowers-marketplace`) plus this repo's own agents and skills under `.claude/`.

| Skill | When |
|---|---|
| `superpowers:subagent-driven-development` | Executing a milestone spine task-by-task (the controller loop) |
| `superpowers:test-driven-development` | Every task — steps are RED/GREEN-shaped and assume it |
| `superpowers:verification-before-completion` | Before claiming any task or milestone complete — run the gates, paste real output |
| `superpowers:requesting-code-review` | Shaping the review package for the `reviewer` agent |
| `superpowers:writing-plans` | Writing the next milestone's task briefs at the previous milestone's gate |
| `superpowers:using-git-worktrees` | Isolating each milestone branch |
| `/gates`, `/milestone-gate`, `/new-prompt-version`, `/cowrie-fixture` | Project skills in `.claude/skills/` |

**Format note:** briefs follow `superpowers:writing-plans` (Goal, Interfaces, bite-sized checkbox
TDD steps, exact commands with expected output), adapted at program scale: they pin exact paths,
signatures, DTO fields, `operation_id`s, named test behaviors, and commands — not full literal
implementations of future code. The PRD plus each brief's **Interfaces** block carry the
authority; on any conflict the PRD wins. Brief skeleton: YAML frontmatter (`id, milestone,
depends_on, status, spec`) → Goal → Context (read ONLY these) → Files → Interfaces (Consumes /
Produces exactly) → Steps (TDD) → Verify → Acceptance.

## Execution model (per task)

Three separate agents per task, defined in `.claude/agents/` — one responsibility each
(`CLAUDE.md` working rules):

1. **`test-author`** — executes the brief's RED steps: writes the failing tests it names, runs
   them, records the failure evidence, sha256-pins each authored file, commits `test(...) RED`.
2. **`implementer`** — executes the GREEN steps: makes the authored tests pass. May add tests;
   may NOT weaken, modify, or delete a pinned test without controller approval (it stops and
   asks). Runs the type-checker after every significant change; all gates before commit.
3. **`reviewer`** — never the implementer; verifies spec compliance and quality against the brief
   with `file:line` evidence, runs the gates independently with cold caches, mutation-tests a
   load-bearing assertion. **A review is never a rubber stamp**: Critical/Important findings get
   a fix round and a re-review; Minors are ledgered for the milestone's final review.

Model policy: `sonnet` for the test-author's and implementer's first iterations; the reviewer
inherits the session model; milestone final reviews run on the strongest model available. Fresh
agent per task; artifacts move as files, not chat context.

**Ledger and artifacts.** At the start of a milestone the controller runs
`superpowers:subagent-driven-development`'s `scripts/sdd-workspace docs/plans/mN-<slug>.md`,
which resolves `.superpowers/sdd/mN-<slug>/` (gitignored). Inside it:

- `progress.md` — the ledger. First line: `# SDD ledger — plan: docs/plans/mN-<slug>.md`. One
  line per event: `Task N: complete (commits <base7>..<head7>, review clean)`, `Task N: fix round
  R/5 (...)`, `Task N: minor (deferred): ...`, `Ruling: ...`, `Acceptance: <clause> → <evidence>`.
  After context compaction, trust the ledger and `git log` over recollection.
- `task-NN-test-author.md`, `task-NN-implementer.md`, `task-NN-review.md` — the three reports.
- `review-<base7>..<head7>.diff` — review packages (`scripts/review-package`).

**Every dispatch** carries the brief path, the report path, the two commit trailers, and the
verbatim env-export line (`export TEST_DATABASE_URL=…`). pytest evidence without that line is
invalid.

## Milestone gate (`/milestone-gate mN`)

1. Every task in the spine's table has a `complete` ledger line.
2. Controller full-gate run, cold cache (`/gates`).
3. Whole-branch review by the `reviewer` agent on the strongest model, with the full Minors
   ledger; fix wave; confirm pass.
4. PRD §12 acceptance walk: every *Accept* clause demonstrated by a command with its output
   pasted into the ledger.
5. PR `feat/mN-<slug>` → `main`; after merge, tag `mN` on the merge commit.
6. User checkpoint.
7. Write the next milestone's task briefs with `superpowers:writing-plans`; update the status
   column below.

## Milestones

| Milestone | Spine | Scope | Depends on | Briefs | Status |
|---|---|---|---|---|---|
| M0 | [`m0-core-loop.md`](m0-core-loop.md) | Repo scaffold + gates + CI; Settings, Verdict/SessionAlert schemas, fixtures; LLM Protocol + client + fake; prompt v1 + summary + `TriagePipeline`; `worker.triage_one` CLI | — | written | **done** — tag `m0` (PR #1, 2026-09-07) |
| M1 | [`m1-eval-v1.md`](m1-eval-v1.md) | Prompts package fix; golden v1 (20 synthetic, labeled, incl. injection kinds) + loader; scoring; `evals.run` CLI; prompts v2/v3; two comparable rows | M0 | written | **done** — tag `m1` (PR #2, 2026-09-07) |
| M2 | [`m2-service-persistence.md`](m2-service-persistence.md) | Async DB + ORM rows + Alembic 0001 + DB fixtures; `create_app` + healthz + OpenAPI baseline; HMAC ingest + dedup; inline triage + one-transaction persistence; Dockerfile + compose | M1 | written (amended with M1 plan defects) | done — tag `m2` (PR #3) |
| M3 | [`m3-read-path-dashboard.md`](m3-read-path-dashboard.md) | List/detail/stats endpoints; `web/` scaffold (Next 16, Tailwind, Vitest, codegen); `/alerts`, `/alerts/[id]`; seed script; web image | M2 | written (7 briefs; folds the 14 M2 final-review plan defects) | done — tag `m3` (PR #4) |
| M4 | [`m4-tool-calling.md`](m4-tool-calling.md) | Tool registry + truncation + recorded fixtures; all five §6.3 tools; loop cap; trace persistence; timeline on detail page | M3 | written (7 briefs; folds the 11 M3 final-review plan defects) | done — tag `m4` (PR #5, 2026-09-09) |
| M5 | [`m5-queue-routing.md`](m5-queue-routing.md) | Redis + ARQ worker container; <100 ms ingest; retry/poison; two-tier routing; idempotency; healthz Redis ping | M4 | written (5 briefs; folds the 16 M4 final-review plan-defect rules and the ledgered M5 items) | done — tag `m5` (PR #6, 2026-09-11) |
| M6 | [`m6-real-data-deploy.md`](m6-real-data-deploy.md) | Cowrie host + shipper; ingest body cap; ECR/EC2/Caddy/SSM deploy scripts and prod copies; backups + log rotation; owner-run walkthrough + VERIFY.md; 48 h soak | M5 | written (6 briefs; folds the 16 M5 final-review plan-defect rules and the ledgered M5 → M6 items) | **done** — tag `m6` (PR #11, 2026-09-14; deployed 2026-09-12, 48 h soak passed) |
| M7 | [`m7-eval-hardening.md`](m7-eval-hardening.md) | v2 sampler (human labels); recorded tool fixtures; LLM-as-judge; full §7.3 metrics; nightly CI gate + baselines; `docs/results.md` | M6 | written (8 briefs, 2026-09-14; folds the M6 gate's DEFER list as task-08) | in progress — branch `feat/m7-eval-hardening` |
| M8a | [`m8-polish.md`](m8-polish.md) tasks 1–3 | SSE `/stream` + `useAlertStream`; `/stats` (+ `cost_by_day`); `/about` | M5 | written (3 briefs, 2026-09-12) | **merged + deployed 2026-09-14** — PR #12, `main` at `dd195b5`, production on `e7fe9ad` |
| M8b | [`m8-polish.md`](m8-polish.md) tasks 4–7 | Redis rate limiter + retriage; token budget breaker; README final; whole-repo review | M7, M8a | at M7 gate | planned — branch `feat/m8b-polish` |
| M8 | [`m8-polish.md`](m8-polish.md) | SSE; `/stats`, `/about`; Redis rate limiter + retriage; token budget breaker; README final; whole-repo review | M7 | at M7 gate | planned |
| M9 | — | Optional AWS/Terraform migration after 2+ weeks of Phase-1 uptime (PRD §12); planned only if the owner opts in | M8 | — | deferred |

Statuses here and in each spine are a snapshot; **git history and the ledgers are authoritative.**

**M8 is split** (owner decision, 2026-09-12): M8a — the three dashboard tasks — is built on
`feat/m8a-dashboard-live` cut from `main` at tag `m5`, in parallel with M6's soak, because it
touches no file M6 edits. M8b — rate limiter, retriage, token budget, README, whole-repo review —
waits for M7 as PRD §12 orders. Both halves share one spine and one ledger
(`.superpowers/sdd/m8-polish/progress.md`); M8a rebases onto `main` after `m6` merges.

## Standing gates (from the moment they exist)

- From M0 task-01: the five Python gates before every commit; `uv run mypy` after every change.
- From M0 task-02: any new `Settings` field ships with its `.env.example` line in the same commit.
- From M0 task-04: shipped prompt versions are immutable (hash-pinned); changes ship as a new file.
- From M2 task-02: any route/DTO change regenerates `api/openapi.json` in the same commit.
- From M3 task-02: the same change regenerates `web/` codegen in the same commit; web gates before
  every commit touching `web/`.
- From M7: `docs/results.md` is append-only and includes worse runs; CI gate thresholds are set
  from the first full v2 run, never invented earlier.
- Always: every bug fix ships a regression test; no v2 golden label is ever machine-authored.
