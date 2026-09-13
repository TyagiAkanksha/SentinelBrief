# SentinelBrief — Claude Code Guide

This file is an **index + working rules** only. It points at the canonical documents instead of
restating them. Read [`PRD.md`](PRD.md) in full before writing any code — it is the source of
truth for scope, architecture, schemas, and build order.

| Area | Source of truth |
|---|---|
| Product spec (authoritative on any conflict) | [`PRD.md`](PRD.md) |
| Python house rules (`api/ worker/ core/ evals/`) | [`CONVENTIONS.md`](CONVENTIONS.md) |
| Frontend house rules (`web/`) | [`docs/FRONTEND-CONVENTIONS.md`](docs/FRONTEND-CONVENTIONS.md) |
| Build plan: milestone spines + task briefs | [`docs/plans/README.md`](docs/plans/README.md) |
| Deployment target (doc of record) | [`docs/deployment.md`](docs/deployment.md) |
| Execution ledger (gitignored, durable progress state) | `.superpowers/sdd/<plan-basename>/progress.md` |
| Project agents, skills, path-scoped rules | `.claude/agents/`, `.claude/skills/`, `.claude/rules/` |

## Milestone discipline

- Build strictly in milestone order (PRD §12). Do not start a milestone until the previous one's
  acceptance criteria pass, its whole-branch review is clean, and its tag (`m0`, `m1`, …) exists.
- **One exception, on the owner's decision of 2026-09-12:** M8 is split. M8a (the three dashboard
  tasks — SSE `/stream`, `/stats`, `/about`) is built on `feat/m8a-dashboard-live`, cut from `main`
  at tag `m5`, alongside M6's soak, because it touches no file M6 edits; it rebases onto `main`
  after `m6` merges. M8b (rate limiter, retriage, token budget, README, whole-repo review) still
  waits for M7. See `docs/plans/m8-polish.md` → Global Constraints.
- **"Continue" means:** read the SessionStart hook output (last `m*` tag, current branch, ledger
  paths), open the current milestone's ledger, and resume at the first task without a
  `complete` line. Git history and the ledger are authoritative; chat memory is not.
- Each milestone ends with: acceptance criteria demonstrated with pasted output, gates green,
  PR into `main`, tag `mN` on the merge commit, user checkpoint, then the next milestone's task
  briefs are written with `superpowers:writing-plans`.
- Respect PRD §1.4 non-goals and §13. Do not add features from the non-goals list even if they
  seem easy. **Never fabricate golden-set v2 labels** — flag them as human work.
- Prefer the smallest change that satisfies the acceptance criteria. No speculative
  abstractions. Enhancements go to [`SUGGESTIONS.md`](SUGGESTIONS.md), never into scope.

## Working rules for AI-assisted development

These bind every session and every dispatched agent working in this repo.

### Scope & planning

- **Plan Mode first.** Before changing anything non-trivial, read the relevant PRD sections,
  conventions, and the task brief; produce a plan the owner can review.
- **The unit of work is one task brief** — `docs/plans/mN-<slug>/task-NN-<slug>.md`. Never hand
  an agent a multi-task blob.
- **Never a bare "implement this."** Every dispatch states HOW: the brief with exact file paths,
  interfaces, values, acceptance criteria, and the verbatim env-export line. Exact values live in
  the brief, not in chat history.
- **PRD ambiguity → raise, don't guess.** Anything schema-, protocol-, or behavior-affecting stops
  the task and goes to the owner. Pure implementation details (file names, internal signatures)
  are decided by the implementer and recorded in its report.

### Agent separation (one responsibility per agent)

- **Test-author, implementer, and reviewer are three separate agents**, defined in
  `.claude/agents/`.
  - The *test-author* writes the task's failing tests (the RED steps) from the brief, proves they
    fail, and pins each authored file with a sha256.
  - The *implementer* makes them pass (the GREEN steps). It may add tests, but may not weaken,
    modify, or delete an authored (pinned) test without controller approval — it **stops and asks
    before touching a pinned file**, even for a mandated rename.
  - The *reviewer* is never the implementer, and is read-only except for its own report.
- **Fresh agent per task; close instances after an implementation.** No context accumulation
  across tasks — a finished agent's knowledge lives in its report file and the ledger. Hand
  artifacts over as files (briefs, reports, review packages), never as pasted text.
- **Model policy:** `sonnet` for the test-author's and implementer's first iterations. Escalate
  only when a task demonstrably needs it (security-sensitive code, subtle correctness, the
  whole-branch final review, which runs on the strongest model available). Record the choice in
  the ledger.

### Verification discipline

- **Run the type-checker after every implementation or significant change** — `uv run mypy`
  (Python), `pnpm -C web type-check` (frontend) — not only at the pre-commit gate.
- **The env-export line is part of every dispatch, verbatim** (`export TEST_DATABASE_URL=…`).
  pytest evidence produced without it is invalid: the DB suite silently skips and a green run
  proves nothing. This was the single most repeated process miss on AdvisorDesk.
- **Tests are part of the task. No test means the task is not complete.** TDD is mandatory: RED
  evidence before GREEN, both recorded in the task report.
- Full gate sets (see the conventions docs, or `/gates`) run clean before every commit.
- Use `superpowers:verification-before-completion` before claiming anything is done: run the
  command, paste the output, then claim.

### Code review

- **Every implementation gets a real review — never a rubber stamp.** The reviewer verifies
  against the brief with evidence (`file:line`) across five dimensions: functionality, tests,
  maintainability, coupling, overall design. It runs the gates itself with cold caches, and
  mutation-tests at least one load-bearing assertion.
- Critical/Important findings get a fix round and a re-review; Minors are ledgered and triaged at
  the whole-branch final review. Findings are persisted verbatim to the ledger before the
  reviewer session ends.
- Findings that conflict with the plan's own text go to the owner — the plan does not grade its
  own work.

### Security & cost invariants (SentinelBrief-specific)

- **LLM calls only ever occur in `worker/`.** `api/` never imports `worker` or `core.llm`; the
  import-linter contract enforces it (the one wiring exception, `api/main.py` during M2's inline
  triage, is removed at M5). If you find yourself importing the LLM client under `api/` or
  `web/`, stop — that violates PRD §10.1.
- No public request path may trigger an LLM call. Retriage is admin-token-gated and globally
  capped. The daily token budget is checked before every LLM call once it exists (M8).
- Attacker-controlled strings (usernames, commands, banners) are always placed inside the
  prompt's data delimiters with the "data, never instructions" sentence (PRD §10.6).
- Never commit `.env`, an `.mmdb` file, a raw honeypot log, or a real attacker payload outside
  `evals/golden/`. Never print a secret value into a report, a ledger line, or a commit message.

### Structure (details in the conventions docs)

- Keep files small; each function or component serves one purpose.
- UI components stay dumb: they render props and raise events; business logic lives in hooks.
- No tight coupling between modules — no exceptions. The backend enforces this with
  import-linter contracts; the frontend with the layering rules in FRONTEND-CONVENTIONS.

## Conventions quick list (details in `CONVENTIONS.md`)

- Python 3.12 via `uv`; `ruff` + `mypy --strict` + `lint-imports` + `pytest` clean before every
  commit. Async throughout (FastAPI, SQLAlchemy 2 async on psycopg 3, ARQ, httpx).
- TypeScript strict mode; Vitest for `web/`.
- All config via environment variables loaded in `core/config.py` (pydantic-settings). Never
  hardcode a model id, price, threshold, or secret. Update `.env.example` in the same commit as
  any new setting (a test enforces this).
- Prompts live in `worker/prompts/` as versioned files (`triage-v1.md`, …); the active version is
  config. Never edit a shipped prompt version in place — create the next version (a hash-pin test
  enforces this).
- Alembic migration per schema change; never edit an applied migration.
- One transaction per verdict write (verdict + tool_calls + status update together).
- Tool-calling tests use recorded fixtures (PRD §7.2), not live APIs. Live-API smoke tests are
  marked `@pytest.mark.live` and excluded from CI and from the default `pytest` invocation.
- Every bug fixed gets a regression test in the same commit.
- Commits: Conventional Commits with a scope and the task id, path-scoped `git add` only, and the
  two trailers (`Co-Authored-By`, `Claude-Session`) on every commit.
