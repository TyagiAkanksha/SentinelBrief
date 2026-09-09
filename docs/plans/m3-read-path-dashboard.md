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
- **Read routes never share the signed ingest router.** `api/routes/alerts.py::router` is
  `APIRouter(route_class=SignedRoute)` and holds exactly one route (`POST /alerts`); a structural
  test pins that. List/detail/stats routes live on a separate unsigned router (e.g.
  `api/routes/alerts_read.py`), included under the same `/api/v1` prefix. (M2 task-03 review I3.)
- **Read routes take `session: SessionDep`, never bare `Depends(get_session)`** (M2 task-02 ruling I5);
  task-02 adds a structural test that greps `api/routes/*.py` for `Depends(get_session)` and fails on any hit.
- **Coverage is real.** `[tool.coverage.run] concurrency = ["greenlet", "thread"]` landed in the M2 fix wave;
  async DB routes must show real line coverage and no report may carry a "greenlet artifact" footnote.
- **CORS is not touched in M3.** RSC pages fetch server-side via `API_URL` over the compose network;
  `CORS_ORIGINS` matters only for the browser SSE hook at M8.
- **Attacker-controlled text is rendered escaped, everywhere.** `reasoning`, `recommended_action`, and the
  collapsible raw JSON (usernames, commands, banners) are plain text in the DOM; tests pin a `<script>` fragment
  in each reaching the DOM escaped.
- **`reasoning` is attacker-influenced text** (prompts v2/v3 ask the model to quote evidence): the
  dashboard renders it as plain text, never as HTML/markdown, and a test pins that a `<script>`
  fragment in `reasoning` reaches the DOM escaped. (M1 review carry-over, t4 I1.)

## Tasks (briefs written at the M2 gate with `superpowers:writing-plans`)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Read DTOs (`PaginatedResponse[T]`, `ErrorEnvelope`, alert/verdict/stats views), read services with the latest-verdict query and stable tiebreaker, `tests/helpers.py` consolidation | `m3-read-path-dashboard/task-01-read-schemas-services.md` | M2 tag |
| 2 | `core/cache.py` TTL cache seam, unsigned read router (`list_alerts`, `get_alert`, `get_stats`), error-handler MRO/≥500 hardening, OpenAPI hygiene (title/version, `\f`, `HealthResponse`, envelope models), `export_openapi.py --out`, CI drift step, `SessionDep` structural test | `m3-read-path-dashboard/task-02-cache-read-routes-openapi.md` | task-01 |
| 3 | `web/` scaffold: pnpm workspace, Next 16, Tailwind tokens, Vitest, ESLint/Prettier, codegen from `api/openapi.json`, CI web job | `m3-read-path-dashboard/task-03-web-scaffold-codegen-ci.md` | task-02 |
| 4 | `/alerts` queue page (RSC), primitives `Badge`/`DataTable`, empty + error states | `m3-read-path-dashboard/task-04-alerts-queue-page.md` | task-03 |
| 5 | `/alerts/[id]` detail page: reasoning, action, confidence, routing, cost/latency, raw JSON, timeline placeholder | `m3-read-path-dashboard/task-05-alert-detail-page.md` | task-04 |
| 6 | `scripts/seed_dev.py`: fixtures + golden v1 through `insert_alert` + `triage_alert` with `FakeLLMClient` (`--live` opt-in) | `m3-read-path-dashboard/task-06-seed-script.md` | task-02 |
| 7 | `infra/Dockerfile.web` (standalone), `web` compose service, README quickstart update, M3 acceptance + tag | `m3-read-path-dashboard/task-07-web-image-compose-acceptance.md` | task-05, task-06 |

Order: 1 → 2 → 3 → 4 → 5 → 7, with 6 parallel to 3–5. Rationale: DTOs and services first (the
API contract is frozen by their types); the routes and OpenAPI baseline (task-02) must exist before
codegen (task-03) consumes them; pages follow the primitives; the seed script only needs task-02;
the image comes last so the acceptance walk runs through compose. (Split of the spine's original
task-01 into 1+2 ruled at the M2 gate: one reviewer gate could not meaningfully cover services,
cache, routes and the error-handler refactor together.)

## Acceptance walk (PRD §12 M3)

| Clause | Demonstrated by |
|---|---|
| List/detail/stats endpoints | task-01 service tests (filters, latest verdict, tiebreaker) + task-02 route tests (cache TTL, 404 envelope, OpenAPI) |
| Next.js `/alerts` and `/alerts/[id]` incl. reasoning display | task-04/05 component tests + a live browser pass at the user checkpoint |
| Seeded DB renders a browsable queue locally | task-06 seed → task-07 `docker compose up` (api, postgres, web) → screenshot/curl pasted into the ledger |

## Status

done — merged via PR #4, tag `m3` on dbd1cde (2026-09-09); git history and the ledger are authoritative.
