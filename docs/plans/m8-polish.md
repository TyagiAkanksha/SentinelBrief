# m8-polish — Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M8 — **authoritative.** Primary sections: §8 (`/stream` SSE, retriage
with its global cap, in-app Redis rate limits, `429` envelope), §9 (pages 3–4; SSE with 30 s
polling fallback; "nothing triggers compute"), §10.2–§10.3 (retriage gating, daily token budget
with a dashboard banner), §14 (definition of done), §11 (VERIFY additions for rate limits).
**Conventions:** all · `.claude/rules/{api,worker,web,infra}.md`.

**Goal:** live updates on `/alerts` via `GET /api/v1/stream` (Redis pub/sub → SSE) with polling
fallback; `/stats` and `/about`; per-IP rate limiting on public GETs backed by Redis counters
with the standard `429` envelope, verified live from two IPs plus a forged-XFF check;
`POST /api/v1/alerts/{id}/retriage` behind the admin bearer token with a 20/day global cap; the
daily token-budget circuit breaker (alerts stay `pending`, banner on the dashboard); a README a
stranger can follow from clean clone to a working local instance in under 10 minutes; a
whole-repo review.

**Architecture:** `api/routes/stream.py` subscribes to the `verdict.created` channel (M5) and
emits SSE events `{id, severity, category, summary}` with heartbeats; `useAlertStream` consumes
it and falls back to polling the list endpoint. Rate limiter: a small Redis sliding-window
(`INCR` + `EXPIRE` per minute bucket, or a sorted set) behind the Protocol from M5, keyed on the
real client IP (uvicorn `--proxy-headers` trusting Caddy per `docs/deployment.md`). Retriage:
admin bearer compared in constant time; global counter in Redis with a UTC-day key; a new verdict
row (latest wins in the UI). Budget: a Redis counter of tokens per UTC day checked in the worker
before every LLM call; when exceeded the job leaves the alert `pending` and `GET /api/v1/stats`
exposes `budget_exhausted: true`, which the layout renders as a banner.

**Tech Stack:** M7 stack. No new dependencies.

## Global Constraints

M0–M7 Global Constraints apply verbatim. Additionally:

### M8a / M8b split (owner decision, 2026-09-12)

M8 runs in two halves so the dashboard can be built while M6's 48-hour soak is still running.

- **M8a — tasks 1–3** (SSE live updates, `/stats`, `/about`): branch `feat/m8a-dashboard-live`, cut
  from `main` at tag `m5`; briefs written 2026-09-12. **Overlap with M6 (measured at the M8a
  final review, correcting an earlier claim of "no overlap"):** nine files are touched by both
  branches — `.env.example`, `.gitignore`, `api/deps.py`, `api/errors.py`, `api/openapi.json`,
  `core/config.py`, `core/errors.py`, `docs/plans/README.md`,
  `web/src/types/generated/schema.d.ts`. Expect textual conflicts in `api/deps.py` (same import
  line and the same insertion point after `get_enqueue`), `api/errors.py` (adjacent import and
  `STATUS_BY_ERROR` rows) and `.gitignore` (a duplicated `.claude/scheduled_tasks.lock` line);
  the rest auto-merge; the two baselines are regenerated, never merged by hand.
- **M8b — tasks 4–7** (per-IP rate limiter + forged-XFF check, retriage, token-budget breaker,
  README final, whole-repo review): branch `feat/m8b-polish`, cut from `main` after `m7` is tagged;
  briefs written at the M7 gate.
- **The canonical gate on the M8a branch is the M5-era one.** That branch has no
  `honeypot/shipper/`, so `--cov=sentinelbrief_shipper` is NOT part of it until the rebase:
  `uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental
  && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals
  --cov-fail-under=90`, with
  `export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
  TEST_REDIS_URL=redis://127.0.0.1:6380/0` first, and 0 skipped.
- **M8a rebases onto `main` once `m6` merges.** The rebase re-runs `scripts/export_openapi.py` and
  `pnpm -C web codegen` (both baselines move on both branches), adds `--cov=sentinelbrief_shipper`
  to every gate invocation, and re-runs the whole gate set before the PR. The M6 soak check-ins and
  the M6 milestone gate take priority over M8a work whenever they come due.
- **Deploying M8a needs a web image rebuilt with the public API origin.** `NEXT_PUBLIC_API_URL` is
  inlined at `next build` (FRONTEND-CONVENTIONS §6), so the SSE hook only reaches the real API when
  the image is built with `NEXT_PUBLIC_API_URL=https://api.sentinelbrief.tyagiakanksha.com`. Confirm
  `infra/deploy/push_ecr.sh` passes that build arg at the M8a gate; the stream vhost's
  `flush_interval -1` is already in `infra/deploy/prod/Caddyfile` (m6 task-03).

### Everything else

- Rate limits, retriage cap and token budget are settings (`PUBLIC_RATE_LIMIT_PER_MIN`,
  `RETRIAGE_PER_DAY`, `DAILY_TOKEN_BUDGET`); a `0` budget means unlimited and is the dev default.
- The forwarded-IP trust setting on uvicorn is only set to `*` in the production compose file,
  and only because Caddy overwrites `X-Forwarded-For` and `api` is not host-published (PRD
  §10.10). The spoof check in `VERIFY.md` is mandatory before this milestone closes.
- `/about` is written for a recruiter with 60 seconds (PRD §9): three paragraphs, the
  architecture diagram, links to the results table and the repo. No marketing.
- The clean-clone timing is measured for real (a fresh directory, `git clone`, `cp .env.example
  .env`, fill three values, `docker compose up`), with the wall-clock pasted into the ledger.
- The whole-repo review runs on the strongest model with the complete Minors ledger from every
  milestone; its remediation batch is planned as `docs/plans/m8r-*.md` only if the owner opts in.

## Tasks

Tasks 1–3 are M8a (briefs written 2026-09-12); tasks 4–7 are M8b (briefs at the M7 gate).

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | `GET /api/v1/stream` SSE from Redis pub/sub + heartbeats; `useAlertStream` with 30 s polling fallback; queue page live-updates | `m8-polish/task-01-sse-live-updates.md` | m5 tag (M8a) |
| 2 | `/stats` page (volume over time, severity distribution, cost/alert trend, p95 latency, escalation rate); `cost_by_day` on `StatsOut` | `m8-polish/task-02-stats-page.md` | task-01 (M8a) |
| 3 | `/about` page (three paragraphs, architecture diagram, project links) | `m8-polish/task-03-about-page.md` | task-02 (M8a) |
| 4 | Redis-backed per-IP rate limiter on public GETs + retriage route with admin token and 20/day cap + `VERIFY.md` additions | `m8-polish/task-04-rate-limits-retriage.md` | M7 tag (M8b) |
| 5 | Daily token-budget circuit breaker in the worker + `budget_exhausted` in stats + dashboard banner | `m8-polish/task-05-token-budget-breaker.md` | task-02 (M8b) |
| 6 | README final (architecture, quickstart, results link, deployment pointer); clean-clone <10 min proof; `.env.example` final pass | `m8-polish/task-06-readme-clean-clone.md` | tasks 1–5 (M8b) |
| 7 | Whole-repo review (strongest model) with the full Minors ledger; remediation plan if opted in | `m8-polish/task-07-whole-repo-review.md` | task-06 (M8b) |

Order: 1 → 2 → 3 (M8a), then 4 → 5 → 6 → 7 (M8b). Rationale: the M8a three run sequentially
because tasks 2 and 3 both edit the primary nav in `web/src/app/layout.tsx` and both regenerate
nothing else in common; task-04 is independent of them; the breaker needs the stats surface;
the README is written against the finished product; the review closes the project.

## Acceptance walk (PRD §12 M8 + §14)

| Clause | Demonstrated by |
|---|---|
| SSE live updates; `/stats` + `/about` | task-01 test (fallback engages on stream error) + live browser pass; task-02/03 pages |
| Per-IP rate limits (in-app, Redis) | task-04: `VERIFY.md` checks from two real IPs (`200`s then `429`s, separate buckets) and the forged-XFF retry staying `429` |
| Retriage admin-gated and capped | task-04: no token → `401`; valid token → new verdict row; 21st call of the day → `429` |
| Token-budget circuit breaker | task-05: with `DAILY_TOKEN_BUDGET` set low, alerts stay `pending`, banner visible; reset at UTC midnight |
| Clean clone → `docker compose up` + `.env` → working local instance in <10 min | task-06 timing pasted into the ledger and the README |
| §14 definition of done walk | task-07 report: live URL, CI green, nightly gate active, ≥3 published eval runs across ≥2 prompts, resume placeholders replaced (owner) |

## Status

M8a **gate complete 2026-09-13** at `4c2cb08` on `feat/m8a-dashboard-live` (ledger:
`.superpowers/sdd/m8-polish/progress.md`): tasks 01–03 approved, whole-branch review + fix wave
approved, browser pass done; the PR into `main` waits for `m6` to merge, then the branch rebases
(regenerate both baselines, add `--cov=sentinelbrief_shipper`, bump both prod image tags at deploy).
M8b planned — briefs 04–07 pending at the M7 gate; M8b inherits the deferred Minors N1–N4, t01 M9,
t02 M4 and the FRONTEND-CONVENTIONS §3 wording from the M8a final review.
