# m3-read-path-dashboard — Read path + dashboard v0 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M3 — **authoritative.** Primary sections: §8 (list/detail/stats
contracts, cache TTLs, envelope), §9 (pages 1–2, design, "nothing triggers compute"), §4
(Next.js 16 + Tailwind), §11 (web container behind Caddy).
**Conventions:** `CONVENTIONS.md` §3–§5, §8 · `docs/FRONTEND-CONVENTIONS.md` (all) ·
`.claude/rules/{api,web}.md`.

**Goal:** `GET /api/v1/alerts` (paginated, filters `severity_gte`, `category`, `since`,
`escalate`, cached 15 s), `GET /api/v1/alerts/{id}` (alert + latest verdict + tool trace, empty
until M4), `GET /api/v1/stats` (cached 60 s); a `web/` app with `/alerts` (queue sorted by
severity desc then recency, severity badge, category, IP, reasoning excerpt, time) and
`/alerts/[id]` (full reasoning, recommended action, confidence, routing info, cost/latency,
collapsible raw JSON; timeline placeholder wired for M4); a seed script so a fresh local DB
renders a browsable queue.

**Architecture:** read services in `core/services/` (session-first, paginated with a stable
tiebreaker); in-process TTL cache on the two list/stats routes (Redis arrives at M5 — the cache
interface is a Protocol so the swap is one wiring line); `api/openapi.json` regenerated and
`web/` codegen consuming it; RSC pages fetch via `API_URL`; Tailwind tokens; Vitest per
FRONTEND-CONVENTIONS §7; `infra/Dockerfile.web` (standalone output) and a `web` compose service.

**Tech Stack:** M2 stack + pnpm 11 · Next.js 16 (App Router) · React 19 · Tailwind · TypeScript
strict · Vitest 4 + RTL · openapi-typescript 7 · ESLint 9 + Prettier · `node:24-slim` image.

## Global Constraints

M0–M2 Global Constraints apply verbatim (branch `feat/m3-read-path-dashboard`). Additionally:

- **Frontend gates before every commit touching `web/`:** `pnpm -C web lint`, `type-check`,
  `format:check`, `test`; `type-check` after every significant change.
- Any route/DTO change regenerates `api/openapi.json` **and** `web/src/types/generated` in the
  same commit; CI diffs both (web job added in task-02).
- **No page or hook triggers compute** (PRD §9); the read endpoints touch only the DB and the
  cache.
- Pagination envelope: `items, total, page, page_size` via one generic `PaginatedResponse[T]`;
  ordering has a deterministic tiebreaker (id) so pages never drop or duplicate rows.
- Severity and category are never color-only in the UI.
- **`reasoning` is attacker-influenced text** (prompts v2/v3 ask the model to quote evidence): the
  dashboard renders it as plain text, never as HTML/markdown, and a test pins that a `<script>`
  fragment in `reasoning` reaches the DOM escaped. (M1 review carry-over, t4 I1.)

## Tasks (briefs written at the M2 gate with `superpowers:writing-plans`)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Read services + list/detail/stats routes + in-process TTL cache + OpenAPI baseline | `m3-read-path-dashboard/task-01-read-services-routes-cache.md` | M2 tag |
| 2 | `web/` scaffold: pnpm workspace, Next 16, Tailwind tokens, Vitest, ESLint/Prettier, codegen, CI web job | `m3-read-path-dashboard/task-02-web-scaffold-codegen-ci.md` | task-01 |
| 3 | `/alerts` queue page (RSC), primitives `Badge`/`DataTable`, empty + error states | `m3-read-path-dashboard/task-03-alerts-queue-page.md` | task-02 |
| 4 | `/alerts/[id]` detail page: reasoning, action, confidence, routing, cost/latency, raw JSON, timeline placeholder | `m3-read-path-dashboard/task-04-alert-detail-page.md` | task-03 |
| 5 | `scripts/seed_dev.py`: load fixtures + golden v1 through the real pipeline with `FakeLLMClient` or a live key | `m3-read-path-dashboard/task-05-seed-script.md` | task-01 |
| 6 | `infra/Dockerfile.web` (standalone), `web` compose service, README quickstart update | `m3-read-path-dashboard/task-06-web-image-compose.md` | task-04, task-05 |

Order: 1 → 2 → 3 → 4 → 6, with 5 parallel to 2–4. Rationale: the API contract (task-01) must be
frozen and baselined before codegen (task-02) consumes it; pages follow the primitives; the seed
script only needs task-01; the image comes last so the acceptance walk runs through compose.

## Acceptance walk (PRD §12 M3)

| Clause | Demonstrated by |
|---|---|
| List/detail/stats endpoints | task-01 route tests (filters, pagination tiebreaker, cache TTL, 404 envelope) |
| Next.js `/alerts` and `/alerts/[id]` incl. reasoning display | task-03/04 component tests + a live browser pass at the user checkpoint |
| Seeded DB renders a browsable queue locally | task-05 seed → `docker compose up` (api, postgres, web) → screenshot/curl pasted into the ledger |

## Status

planned — briefs pending (written at the M2 gate).
