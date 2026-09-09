---
id: task-04
milestone: m3-read-path-dashboard
depends_on: [task-03]
status: planned
spec: PRD.md §8 (list filters, pagination), §9 (page 1 — /alerts: sorted by severity desc then recency; severity badge, category, source IP, one-line reasoning excerpt, time; dark, dense, legible; nothing triggers compute), §12 M3; docs/FRONTEND-CONVENTIONS.md §2–§4, §6, §7, §9
---

# task-04 — `/alerts` queue page (RSC), primitives `Badge` / `DataTable`, empty + error states

## Goal

`/alerts` renders the queue from `GET /api/v1/alerts` server-side: one row per alert with a
severity badge (text `S4` plus color — never color-only), category badge, source IP, sensor,
one-line reasoning excerpt and a relative time with a UTC title; `?page`, `?page_size` and the
four API filters are read from the URL and forwarded verbatim; pagination links preserve the
filters. A server-rendered filter form (`FilterBar`, no client JS) mirrors the four API filters
(PRD §9 "Filters mirror the API") and round-trips them through the URL. Sorting is the API's
(severity desc, then recency). Every attacker-influenced string (the excerpt, and a filter value
echoed from the URL) reaches the DOM as escaped text. Empty and error states are explicit
components. Nothing on the page triggers compute. The country flag on the IP cell is **not** here:
it arrives in M4 with `get_ip_geo_asn`'s result (M4 spine, Global Constraints).

## Context (read ONLY these)

- `PRD.md` §8 (filters), §9 (page 1 and "Design"), §12 M3.
- `docs/plans/m3-read-path-dashboard.md` — Global Constraints (never color-only, escaped text,
  no compute, frontend gates).
- `docs/FRONTEND-CONVENTIONS.md` §2 (tokens, primitives), §3 (folder-per-component, dumb
  components), §4 (`Badge` owns the severity mapping), §6 (RSC fetch via `API_URL`, no-store),
  §7 (Vitest per file, RTL, test what the user sees), §9 (never color-only, `<caption>`, UTC +
  relative hint, tabular numbers, explicit empty/error components).
- `.claude/rules/web.md`.
- Task-03 outputs: `web/src/lib/api/server.ts` (`getJson`, `ApiError`), `web/src/types/api.ts`
  (`AlertSummary`, `PaginatedAlerts`, `VerdictCategory`, `AlertStatus`), `web/src/app/globals.css`
  (tokens `--color-sev-1..5`, `--color-surface`, `--color-border`, `--color-muted`,
  `--color-accent`), `web/vitest.config.ts`, `web/src/app/layout.tsx`.
- Next 16: `searchParams` is a `Promise<{ [key: string]: string | string[] | undefined }>`
  (verified at briefing time from the v16.3.4 `page.js` reference).

## Files

- Create: `web/src/lib/format.ts`, `web/src/lib/alerts-query.ts`,
  `web/src/components/ui/Badge/{Badge.tsx,interface.ts,index.ts}`,
  `web/src/components/ui/DataTable/{DataTable.tsx,interface.ts,index.ts}`,
  `web/src/components/ui/EmptyState/{EmptyState.tsx,interface.ts,index.ts}`,
  `web/src/components/ui/ErrorState/{ErrorState.tsx,interface.ts,index.ts}`,
  `web/src/components/ui/Pagination/{Pagination.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/AlertRow/{AlertRow.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/AlertQueue/{AlertQueue.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/FilterBar/{FilterBar.tsx,interface.ts,index.ts}`,
  `web/src/app/alerts/page.tsx`
- Create (test-author, colocated per FRONTEND-CONVENTIONS §7 — ten files): `web/src/lib/format.test.ts`,
  `web/src/lib/alerts-query.test.ts`, `web/src/components/ui/Badge/Badge.test.tsx`,
  `web/src/components/ui/DataTable/DataTable.test.tsx`,
  `web/src/components/ui/EmptyState/EmptyState.test.tsx`,
  `web/src/components/ui/ErrorState/ErrorState.test.tsx`,
  `web/src/components/ui/Pagination/Pagination.test.tsx`,
  `web/src/components/alerts/AlertRow/AlertRow.test.tsx`,
  `web/src/components/alerts/AlertQueue/AlertQueue.test.tsx`,
  `web/src/components/alerts/FilterBar/FilterBar.test.tsx`
- Modify: none (task-03's `page.tsx` redirect already targets `/alerts`)

## Interfaces

- **Consumes:** `getJson`, `ApiError` (`web/src/lib/api/server.ts`); `AlertSummary`,
  `PaginatedAlerts`, `VerdictCategory`, `AlertStatus` (`web/src/types/api.ts`); the token
  utilities `bg-sev-1..5`, `text-sev-1..5`, `border-sev-1..5`, `bg-surface`, `border-border`,
  `text-muted`, `text-accent`, `font-mono` (task-03 `globals.css`); `next/link`.
- **Produces (later tasks rely on — produce exactly):**

  ```ts
  // web/src/lib/format.ts — pure; every function is total (never throws on null/invalid)
  export function formatUtc(iso: string): string;            // "2026-09-06 01:00:00Z" (UTC, seconds precision); invalid date -> "—"
  export function formatAge(iso: string, now: Date): string;  // "<1m ago" (< 60 s) | "Nm ago" (< 60 min) | "Nh ago" (< 24 h) | "Nd ago"; future or invalid -> "—"
  export function formatUsd(value: string | null): string;    // "$" + Number(value).toFixed(6) -> "$0.000228"; null or NaN -> "—"
  export function formatPercent(confidence: number): string;  // `${Math.round(confidence * 100)}%` -> "90%"
  export function formatLatency(ms: number | null): string;   // `${ms.toLocaleString("en-US")} ms` -> "1,234 ms"; null -> "—"
  export function formatTokens(n: number | null): string;     // n.toLocaleString("en-US") -> "1,234"; null -> "—"

  // web/src/lib/alerts-query.ts — URL <-> API query, pure
  export type SearchParams = Record<string, string | string[] | undefined>;
  export type ListQuery = { page: number; page_size: number; severity_gte?: number; category?: string; since?: string; escalate?: boolean };
  export const DEFAULT_PAGE_SIZE = 25;
  export function parseListQuery(sp: SearchParams): ListQuery;
    // arrays -> first element; page: parseInt, NaN or < 1 -> 1; page_size: NaN -> 25, clamped to [1, 100];
    // severity_gte: integer in 1..5 else omitted; category / since: non-empty string passed through (the API validates);
    // escalate: "true" -> true, "false" -> false, anything else omitted
  export function toQueryString(q: ListQuery): string;        // keys sorted, undefined omitted, URLSearchParams encoding -> "page=1&page_size=25&severity_gte=4"
  export function pageHref(q: ListQuery, page: number): string;   // `/alerts?${toQueryString({ ...q, page })}`

  // web/src/components/ui/Badge/interface.ts
  export type Severity = 1 | 2 | 3 | 4 | 5;
  export type BadgeProps = { severity: Severity } | { category: VerdictCategory };
  // index.ts (every component folder): export { Badge } from "./Badge"; export type { BadgeProps, Severity } from "./interface";
  //   — task-05 imports `Severity` from "@/components/ui/Badge"
  // Badge.tsx: <span class="inline-block rounded border px-1.5 font-mono text-xs ...">
  //   severity -> text `S${severity}`, aria-label `Severity ${severity}`, title the same, classes from
  //   SEVERITY_CLASSES: Record<Severity, string> (e.g. 4 -> "border-sev-4 bg-sev-4/20 text-sev-4") — the ONLY place that maps severity to color
  //   category -> text = the category value verbatim (e.g. "brute_force"), classes "border-border bg-surface text-muted"

  // web/src/components/ui/DataTable/interface.ts
  export type Column = { key: string; header: string; className?: string };
  export type DataTableProps<Row> = {
    caption: string;                       // rendered as <caption class="sr-only"> (FRONTEND-CONVENTIONS §9)
    columns: readonly Column[];            // <th scope="col"> per column
    rows: readonly Row[];
    rowKey: (row: Row) => string;
    renderRow: (row: Row) => ReactNode;    // returns a <tr> with one <td> per column, in column order
    emptyMessage: string;                  // rows.length === 0 -> one <tr><td colSpan={columns.length}>{emptyMessage}</td></tr>
  };
  // DataTable.tsx: export function DataTable<Row>(props: DataTableProps<Row>): JSX — <div class="overflow-x-auto"><table class="w-full text-sm">…

  // web/src/components/ui/EmptyState/interface.ts   export type EmptyStateProps = { message: string };   // <p role="status">{message}</p>
  // web/src/components/ui/ErrorState/interface.ts   export type ErrorStateProps = { title: string; detail: string };   // <div role="alert"><h2>{title}</h2><p>{detail}</p></div>

  // web/src/components/ui/Pagination/interface.ts
  export type PaginationProps = { page: number; pageSize: number; total: number; hrefForPage: (page: number) => string };
  // Pagination.tsx: <nav aria-label="Pagination"> "Page {page} of {pages} · {total} alerts" where pages = max(1, ceil(total / pageSize));
  //   <Link href={hrefForPage(page - 1)}>Previous</Link> only when page > 1; <Link href={hrefForPage(page + 1)}>Next</Link> only when page < pages

  // web/src/components/alerts/AlertRow/interface.ts
  export type AlertRowProps = { alert: AlertSummary; now: Date };
  // AlertRow.tsx renders ONE <tr> with six <td>, in ALERT_COLUMNS order:
  //   1 severity:  alert.verdict ? <Badge severity={alert.verdict.severity as Severity} /> : <span class="text-muted">{alert.status}</span>
  //   2 category:  alert.verdict ? <Badge category={alert.verdict.category} /> : "—"
  //   3 source ip: <Link href={`/alerts/${alert.id}`} class="font-mono text-accent">{alert.src_ip}</Link>
  //   4 sensor:    {alert.sensor}
  //   5 reasoning: alert.verdict ? {alert.verdict.reasoning_excerpt} : (status === "pending" ? "awaiting triage" : "triage failed — no verdict")
  //   6 received:  <time dateTime={alert.received_at} title={formatUtc(alert.received_at)}>{formatAge(alert.received_at, now)}</time>
  //   All text via JSX expressions — never dangerouslySetInnerHTML anywhere under web/src.

  // web/src/components/alerts/AlertQueue/interface.ts
  export const ALERT_COLUMNS: readonly Column[];   // keys: severity, category, src_ip, sensor, reasoning, received (six)
  export type AlertQueueProps = { page: PaginatedAlerts | null; error: Error | null; now: Date; hrefForPage: (page: number) => string };
  // AlertQueue.tsx:
  //   error !== null -> error instanceof ApiError
  //       ? <ErrorState title={`API error ${error.status}`} detail={error.envelope?.error.message ?? error.message} />
  //       : <ErrorState title="API unreachable" detail={error.message} />
  //   page === null || page.items.length === 0 -> <EmptyState message="No alerts yet — run scripts/seed_dev.py" />
  //   else <DataTable caption="Alert queue, sorted by severity then recency" columns={ALERT_COLUMNS} rows={page.items}
  //          rowKey={(a) => a.id} renderRow={(a) => <AlertRow key={a.id} alert={a} now={now} />} emptyMessage="No alerts" />
  //        + <Pagination page={page.page} pageSize={page.page_size} total={page.total} hrefForPage={hrefForPage} />

  // web/src/components/alerts/FilterBar/interface.ts
  export type FilterBarProps = { query: ListQuery };
  export const VERDICT_CATEGORIES: readonly VerdictCategory[];   // the seven literals, in PRD §6.5 order; `satisfies readonly VerdictCategory[]` so tsc checks each against the generated union
  // FilterBar.tsx — server-rendered, no client JS (no "use client", no event handlers): PRD §9 "filters mirror the API"
  //   <form method="get" action="/alerts" aria-label="Filters">
  //     <label>Minimum severity <select name="severity_gte" defaultValue={query.severity_gte?.toString() ?? ""}>
  //         <option value="">any</option> then <option value="1">S1</option> … <option value="5">S5</option></select></label>
  //     <label>Category <select name="category" defaultValue={query.category ?? ""}>
  //         <option value="">any</option> then one <option value={c}>{c}</option> per VERDICT_CATEGORIES entry, verbatim;
  //         when query.category is set and is not one of the seven, one extra <option value={query.category}>{query.category}</option>
  //         is appended so the URL state is shown faithfully — as escaped text (the value comes from the URL)</select></label>
  //     <label>Escalated <select name="escalate" defaultValue={query.escalate === undefined ? "" : String(query.escalate)}>
  //         <option value="">any</option><option value="true">true</option><option value="false">false</option></select></label>
  //     <label>Since <input type="datetime-local" name="since" defaultValue={query.since ?? ""} /></label>
  //     <input type="hidden" name="page_size" value={query.page_size} />
  //     <button type="submit">Apply</button> <a href="/alerts">Clear</a>
  //   </form>
  //   — never a `page` input: a new filter starts at page 1 (parseListQuery defaults it). Empty "" selections submit as
  //     empty params, which parseListQuery already treats as absent. defaultValue renders `selected` server-side.

  // web/src/app/alerts/page.tsx — React Server Component; no "use client" anywhere in M3
  export const dynamic = "force-dynamic";
  export default async function AlertsPage({ searchParams }: { searchParams: Promise<SearchParams> }): Promise<JSX.Element>;
    // const query = parseListQuery(await searchParams); const now = new Date();
    // let page: PaginatedAlerts | null = null; let error: Error | null = null;
    // try { page = await getJson<PaginatedAlerts>(`/api/v1/alerts?${toQueryString(query)}`); }
    // catch (e) { error = e instanceof Error ? e : new Error(String(e)); }
    // <section><h1 class="text-lg font-semibold">Alert queue</h1>
    //   <FilterBar query={query} />        — placed in the PAGE, above AlertQueue, so it also renders in the empty and
    //                                        error states (a filter that matches nothing, or one the API rejects, can be cleared)
    //   <AlertQueue page={page} error={error} now={now} hrefForPage={(p) => pageHref(query, p)} /></section>
    // Filters round-trip through the URL (?severity_gte=4&category=brute_force&escalate=true&since=…): FilterBar submits
    // them, parseListQuery reads them, pageHref preserves them.
  ```

## Interfaces → test table

Vitest names are the `it(...)` strings; component files carry `// @vitest-environment jsdom` on
line 1; `format.test.ts` and `alerts-query.test.ts` run in `node`.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `formatUtc` | `web/src/lib/format.test.ts` — `it("formatUtc renders UTC with seconds and a Z suffix")` | `"2026-09-06T01:00:00.000Z"` → `"2026-09-06 01:00:00Z"`; `"nope"` → `"—"` |
| `formatAge` | `format.test.ts` — `it("formatAge buckets seconds, minutes, hours and days")` | 30 s → `"<1m ago"`, 3 min → `"3m ago"`, 2 h → `"2h ago"`, 5 d → `"5d ago"`, future → `"—"` |
| `formatUsd` | `format.test.ts` — `it("formatUsd formats the decimal string with six places")` | `"0.000228"` → `"$0.000228"`; `null` → `"—"`; `"x"` → `"—"` |
| `formatPercent` | `format.test.ts` — `it("formatPercent rounds to a whole percent")` | `0.9` → `"90%"`, `0.955` → `"96%"` |
| `formatLatency` | `format.test.ts` — `it("formatLatency uses thousands separators and a ms suffix")` | `1234` → `"1,234 ms"`; `null` → `"—"` |
| `formatTokens` | `format.test.ts` — `it("formatTokens uses thousands separators")` | `1234` → `"1,234"`; `null` → `"—"` |
| `parseListQuery` defaults | `web/src/lib/alerts-query.test.ts` — `it("defaults to page 1 and page_size 25")` | `{}` → `{ page: 1, page_size: 25 }` (no filter keys) |
| `parseListQuery` coercion | `alerts-query.test.ts` — `it("coerces an invalid page to 1 and clamps page_size to 1..100")` | `page="abc"`, `page="0"` → 1; `page_size="500"` → 100; `page_size="0"` → 1 |
| filters pass-through | `alerts-query.test.ts` — `it("passes severity_gte, category, since and escalate through")` | `severity_gte="4"` → 4, `category="brute_force"`, `since="2026-09-01T00:00:00Z"`, `escalate="true"` → true; arrays take the first element |
| filter rejection | `alerts-query.test.ts` — `it("drops severity_gte outside 1..5 and escalate values other than true/false")` | `severity_gte="9"` and `escalate="maybe"` absent |
| `toQueryString` | `alerts-query.test.ts` — `it("toQueryString sorts keys and omits undefined")` | `{ page: 2, page_size: 25, severity_gte: 4 }` → `"page=2&page_size=25&severity_gte=4"` |
| `pageHref` | `alerts-query.test.ts` — `it("pageHref keeps every filter and replaces page")` | `/alerts?category=scanning&page=3&page_size=25` |
| severity badge text | `web/src/components/ui/Badge/Badge.test.tsx` — `it("renders S1..S5 as text for every severity")` | loop 1–5: `getByText("S4")` |
| severity badge a11y | `Badge.test.tsx` — `it("exposes an accessible severity label")` | `getByLabelText("Severity 4")` |
| category badge | `Badge.test.tsx` — `it("renders the category text verbatim")` | `getByText("brute_force")` |
| `DataTable` caption | `web/src/components/ui/DataTable/DataTable.test.tsx` — `it("renders the caption")` | `getByText(caption)` inside a `caption` element |
| header cells | `DataTable.test.tsx` — `it("renders one header cell per column")` | `getAllByRole("columnheader").length === columns.length` |
| empty rows | `DataTable.test.tsx` — `it("renders the empty message when there are no rows")` | `getByText(emptyMessage)` with `colSpan === columns.length` |
| `renderRow` | `DataTable.test.tsx` — `it("renders one row per item via renderRow")` | 3 rows → `getAllByRole("row").length === 4` (header + 3) |
| `EmptyState` | `web/src/components/ui/EmptyState/EmptyState.test.tsx` — `it("renders the message with role=status")` | `getByRole("status")` text |
| `ErrorState` | `web/src/components/ui/ErrorState/ErrorState.test.tsx` — `it("renders title and detail with role=alert")` | `getByRole("alert")` contains both |
| `Pagination` first page | `web/src/components/ui/Pagination/Pagination.test.tsx` — `it("hides Previous on the first page")` | `queryByText("Previous") === null`, `getByText("Next")` |
| `Pagination` last page | `Pagination.test.tsx` — `it("hides Next on the last page")` | page 5 of 5 |
| `Pagination` hrefs | `Pagination.test.tsx` — `it("links Previous and Next via hrefForPage and prints the summary")` | page 2, total 112, size 25 → `"Page 2 of 5 · 112 alerts"`, `Previous` href `hrefForPage(1)`, `Next` href `hrefForPage(3)` |
| `AlertRow` text | `web/src/components/alerts/AlertRow/AlertRow.test.tsx` — `it("renders src_ip, sensor and the reasoning excerpt as text")` | all three via `getByText`; link href `/alerts/<id>` |
| `AlertRow` escaping | `AlertRow.test.tsx` — `it("escapes a <script> fragment in reasoning_excerpt")` | excerpt `"<script>alert(1)</script>"` → `getByText("<script>alert(1)</script>")` present and `container.querySelector("script") === null` |
| `AlertRow` no verdict | `AlertRow.test.tsx` — `it("shows the status instead of a badge when there is no verdict")` | `verdict: null, status: "pending"` → text `pending` and `awaiting triage`, no `S` badge |
| `AlertRow` time | `AlertRow.test.tsx` — `it("renders a relative age with the UTC timestamp as title")` | `<time>` text `"3m ago"`, `title` `"… Z"`, `dateTime === received_at` |
| `AlertQueue` empty | `web/src/components/alerts/AlertQueue/AlertQueue.test.tsx` — `it("renders the empty state when items is empty")` | text `No alerts yet — run scripts/seed_dev.py` |
| `AlertQueue` ApiError | `AlertQueue.test.tsx` — `it("renders the ApiError envelope message in the error state")` | `new ApiError(503, {error:{code:"internal_error",message:"db down"}})` → `API error 503` + `db down` |
| `AlertQueue` other error | `AlertQueue.test.tsx` — `it("renders the unreachable state for a non-API error")` | `new Error("ECONNREFUSED")` → `API unreachable` + `ECONNREFUSED` |
| `AlertQueue` table | `AlertQueue.test.tsx` — `it("renders a table with one row per alert and pagination")` | 2 items → 3 `row`s, `getByRole("navigation", { name: "Pagination" })` |
| `FilterBar` pre-fill + categories | `web/src/components/alerts/FilterBar/FilterBar.test.tsx` — `it("renders the four labelled controls pre-filled from query with seven category options")` | query `{ severity_gte: 4, category: "brute_force", escalate: true, since: "2026-09-01T00:00" }` → `getByLabelText("Minimum severity")` value `"4"`, `Category` value `"brute_force"` with 8 options (any + 7), `Escalated` value `"true"`, `Since` value `"2026-09-01T00:00"` |
| `FilterBar` absent filters | `FilterBar.test.tsx` — `it("selects any for every absent filter")` | query `{ page: 1, page_size: 25 }` → the three selects have value `""` (option text `any` selected) and `Since` is `""` |
| `FilterBar` form shape | `FilterBar.test.tsx` — `it("submits with method=get to /alerts, carries page_size but never page, and offers a Clear link")` | `getByRole("form", { name: "Filters" })` has `method="get"`, `action="/alerts"`; `input[name="page"]` absent; hidden `page_size` value `"25"`; `getByRole("button", { name: "Apply" })`; `getByRole("link", { name: "Clear" })` href `/alerts` |
| `FilterBar` escaping | `FilterBar.test.tsx` — `it("escapes a <script> fragment in query.category")` | `category: "<script>alert(1)</script>"` → an option with that text is selected, `container.querySelector("script") === null` |
| `page.tsx` | no unit test (async RSC); exercised by task-07's acceptance walk (`curl localhost:3000/alerts` shows `S4`/`S5` and the `Filters` form, `?severity_gte=5` shows only `S5`) | — |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the ten test files; the **implementer** does
Steps 3–6 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the ten test files** per the table. Component tests
  import from the barrel (`@/components/ui/Badge`) and render with RTL; `AlertRow` renders inside
  `<table><tbody>…</tbody></table>`; `AlertQueue`/`AlertRow` fixtures build an `AlertSummary`
  literal with every field (`id`, `source: "cowrie"`, `src_ip`, `sensor`, `event_time`,
  `received_at`, `status`, `verdict`) so `tsc` checks them against the generated type. Format with
  `pnpm -C web format` (prettier exists since task-03) and check `pnpm -C web lint`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `pnpm -C web test` → Expected: 10 new
  files fail with `Failed to resolve import` for, respectively, `@/lib/format`,
  `@/lib/alerts-query`, `@/components/ui/Badge`, `@/components/ui/DataTable`,
  `@/components/ui/EmptyState`, `@/components/ui/ErrorState`, `@/components/ui/Pagination`,
  `@/components/alerts/AlertRow`, `@/components/alerts/AlertQueue`,
  `@/components/alerts/FilterBar`; the task-03 tests stay green
  (`Test Files 10 failed | 2 passed`). Pin, commit
  `test(web): queue page primitives and formatting RED (m3 task-04)`.
- [ ] **Step 3 (GREEN — implementer): `src/lib/format.ts`, `src/lib/alerts-query.ts`.**
  `pnpm -C web test src/lib` green; `pnpm -C web type-check` clean.
- [ ] **Step 4 (GREEN — implementer): primitives** `Badge`, `DataTable`, `EmptyState`,
  `ErrorState`, `Pagination` (folder-per-component with `interface.ts` + `index.ts` barrels).
  `type-check` clean.
- [ ] **Step 5 (GREEN — implementer): `AlertRow`, `AlertQueue`, `FilterBar`,
  `src/app/alerts/page.tsx`.** `pnpm -C web test` → `Test Files 12 passed (12) / Tests 43 passed
  (43)`.
- [ ] **Step 6 (implementer): render against the real API once** — `docker compose -f
  infra/docker-compose.yml up -d postgres api` (or the task-02 dev server) with a couple of signed
  POSTs from `scripts/post_alert.py`, `API_URL=http://127.0.0.1:8000 pnpm -C web dev`, open
  `/alerts` and `/alerts?severity_gte=4` — paste the `curl -s localhost:3000/alerts | grep -o
  'S[1-5]' | sort | uniq -c` output into the report. Then all gates (Python five + the four web
  gates) → commit:
  `feat(web): /alerts queue page with Badge, DataTable, AlertRow, AlertQueue, pagination (m3 task-04)`
  with the two trailers; path-scoped `git add web/src`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test   # Test Files 12 passed (12) / Tests 43 passed (43)
grep -rn "dangerouslySetInnerHTML\|use client" web/src ; echo "exit=$?"                        # exit=1 (neither appears)
grep -rln "#[0-9a-fA-F]\{6\}" web/src                                                          # web/src/app/globals.css only
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean (no Python changed; gates still run before the commit)
```

## Acceptance

- `/alerts` lists the API's page in the API's order with badge text `S1`–`S5` and the category
  text, IP, sensor, excerpt and relative time; `?page`/`?page_size`/`?severity_gte`/`?category`/
  `?since`/`?escalate` reach the API unchanged and survive pagination links.
- The queue's filter form round-trips every API filter through the URL: submitting starts at
  page 1, `Clear` returns to `/alerts`, and a value the API rejects is shown escaped in the form
  and answered by the error state. The country flag on the IP cell is M4's (`get_ip_geo_asn`).
- A `<script>` fragment in `reasoning_excerpt` (or in a filter value echoed from the URL) is
  visible as text and never becomes an element; empty and error states are explicit components;
  no component maps severity to color except `Badge`; no page or component calls anything but
  `GET` read endpoints.
