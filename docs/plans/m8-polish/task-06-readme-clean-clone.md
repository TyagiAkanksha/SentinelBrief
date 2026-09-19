---
id: task-06
milestone: m8-polish
depends_on: [task-01, task-02, task-03, task-04, task-05]
status: planned
spec: PRD.md §12 M8 ("clean clone → docker compose up + .env → working local instance in <10 min"), §9 (dashboard/about audience), §1.3 (honest framing), §14 (definition of done); `docs/plans/m8-polish.md` Global Constraints (clean-clone measured for real, wall-clock in the ledger + README); `CONVENTIONS.md` §7 (`.env.example` documents every Setting — a test enforces it)
---

# task-06 — README final pass, `.env.example` final pass, and a measured clean-clone <10-minute proof

## Goal
The README becomes the front door: one-paragraph what/why, the architecture diagram (PRD §3), a
dev quickstart that a stranger can follow (prereqs with versions, `cp .env.example .env`, the three
values to fill — `INGEST_HMAC_SECRET`, `LLM_API_KEY`, `MODEL_PRICES_JSON` — `docker compose up`, the
migrate one-off, verify curls), the five Python + four web gates, a deployment pointer
(`docs/deployment.md`), the results link (`docs/results.md`, real v2 numbers from the M7 sitting),
and an honest "what it does not do" (PRD §1.4 non-goals). `.env.example` gets a final pass so EVERY
Setting added across M0–M8 is present, documented, defaulted, secrets blank, later-milestone vars
annotated. Then a REAL clean-clone timing: fresh dir, `git clone`, `cp .env.example .env`, fill the
three values, `docker compose up -d --build`, migrate, first successful triage — wall-clock pasted
into the ledger and the README, must be < 10 min.

## Context (read ONLY these)
- `PRD.md` §1.3, §1.4, §3, §9, §12 M8, §14. `docs/plans/m8-polish.md`. `CONVENTIONS.md` §7. `README.md` (current), `.env.example`, `docs/deployment.md`, `docs/results.md`.
- The clean-clone runs against the PUBLIC repo (private until M8b; the whole-repo review/task-07 or an owner step flips visibility — coordinate). Use the real GitHub clone URL.

## Interfaces
No code interfaces — this is docs + a measured procedure. `.env.example` must satisfy
`tests/test_env_example_roster.py::test_every_settings_field_documented_in_env_example` after M8b's
new settings (`public_rate_limit_per_min`, `retriage_per_day`, `admin_token`, `daily_token_budget`,
`retriage_lock_timeout_ms`). The README's results link and gate command block must match the
canonical gate (with `--cov=sentinelbrief_shipper`).

## Folds these deferred findings (the `web/`-facing ones from the M8a final review)
- N1 class-only done-callback for the post-cancel teardown exception (if not done in task-04/05).
- N2 pin the settle ref-read; header gutter alignment; replace `NavLink as Link` + the two
  source-text nav tests with a render-tested `PrimaryNav` (t02 M4); FRONTEND-CONVENTIONS §3 wording.
- LiveIndicator announce-per-alert (live-region politeness).
  (Any of these still open at task-06 land here as small `web/` fixes with tests; each cites its
  M8a-review finding id.)

## Steps (TDD outline)
- test-author: extend `tests/test_env_example_roster.py` coverage is automatic (it enumerates
  Settings); add/adjust `web/` render tests for `PrimaryNav`/LiveIndicator per the folded findings;
  a `tests/test_readme_doc.py` check that the README links `docs/results.md` and `docs/deployment.md`
  and lists the canonical gates.
- implementer: rewrite README sections, final `.env.example` pass, the folded `web/` fixes.
- controller (measured): the clean-clone timing run, wall-clock pasted into the ledger + README.

## Verify
`uv run pytest -q tests/test_env_example_roster.py tests/test_readme_doc.py`; `pnpm -C web test`;
the clean-clone stopwatch procedure end-to-end < 10 min with the number recorded.

## Acceptance
A stranger can go clone → running local instance in < 10 min (proven, timed); `.env.example`
documents every Setting; the README is honest and complete with working results + deployment links;
the folded `web/` Minors are closed.
