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

M0–M7 Global Constraints apply verbatim (branch `feat/m8-polish`). Additionally:

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

## Tasks (briefs written at the M7 gate)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | `GET /api/v1/stream` SSE from Redis pub/sub + heartbeats; `useAlertStream` with 30 s polling fallback; queue page live-updates | `m8-polish/task-01-sse-live-updates.md` | M7 tag |
| 2 | `/stats` page (volume over time, severity distribution, cost/alert trend, p95 latency, escalation rate) | `m8-polish/task-02-stats-page.md` | M7 tag |
| 3 | `/about` page | `m8-polish/task-03-about-page.md` | M7 tag |
| 4 | Redis-backed per-IP rate limiter on public GETs + retriage route with admin token and 20/day cap + `VERIFY.md` additions | `m8-polish/task-04-rate-limits-retriage.md` | M7 tag |
| 5 | Daily token-budget circuit breaker in the worker + `budget_exhausted` in stats + dashboard banner | `m8-polish/task-05-token-budget-breaker.md` | task-02 |
| 6 | README final (architecture, quickstart, results link, deployment pointer); clean-clone <10 min proof; `.env.example` final pass | `m8-polish/task-06-readme-clean-clone.md` | tasks 1–5 |
| 7 | Whole-repo review (strongest model) with the full Minors ledger; remediation plan if opted in | `m8-polish/task-07-whole-repo-review.md` | task-6 |

Order: (1, 2, 3, 4 in parallel) → 5 → 6 → 7. Rationale: the four features are independent; the
breaker needs the stats surface; the README is written against the finished product; the review
closes the project.

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

planned — briefs pending (written at the M7 gate).
