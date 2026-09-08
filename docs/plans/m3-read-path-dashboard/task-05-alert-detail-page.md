---
id: task-05
milestone: m3-read-path-dashboard
depends_on: [task-04]
status: planned
spec: PRD.md §8 (`GET /api/v1/alerts/{id}` = alert + latest verdict + tool trace), §9 (page 2 — /alerts/[id]: full reasoning, recommended action, confidence, tool-call timeline, model routing info, cost & latency, collapsible raw JSON), §10.6 (attacker-controlled strings), §12 M3 ("incl. reasoning display"); docs/FRONTEND-CONVENTIONS.md §3, §4 (Timeline is an `<ol>`), §7, §9
---

# task-05 — `/alerts/[id]` detail page: reasoning, action, confidence, routing, cost/latency, raw JSON, timeline placeholder

## Goal

`/alerts/[id]` renders one alert from `GET /api/v1/alerts/{id}` server-side: the verdict panel
(severity + category badges, confidence as `NN%`, the escalate flag, the **full** `reasoning` and
`recommended_action` as plain escaped text), routing info (primary/final model, escalation,
prompt version, tokens, cost from the decimal string, latency), the collapsible raw session JSON
(escaped — usernames and commands are attacker-controlled), a `<ol aria-label="Tool trace">`
timeline that M4 fills from `tool_calls` (one placeholder item until then), and a status banner for
`pending`/`failed` alerts that have no verdict. A 404 (or a non-UUID id → 422) from the API becomes
Next's `notFound()` page. Nothing on the page triggers compute.

## Context (read ONLY these)

- `PRD.md` §8 (detail row), §9 (page 2, "Design"), §10.6, §12 M3.
- `docs/plans/m3-read-path-dashboard.md` — Global Constraints (escaped text everywhere:
  `reasoning`, `recommended_action`, raw JSON; never color-only; no compute).
- `docs/FRONTEND-CONVENTIONS.md` §3, §4, §7, §9; `.claude/rules/web.md`.
- Task-03 outputs: `web/src/lib/api/server.ts` (`getJson`, `ApiError`), `web/src/types/api.ts`
  (`AlertDetail`, `VerdictOut`, `ToolCallOut`, `AlertStatus`).
- Task-04 outputs: `web/src/lib/format.ts` (`formatUtc`, `formatAge`, `formatUsd`,
  `formatPercent`, `formatLatency`, `formatTokens`), `web/src/components/ui/Badge`
  (`Badge`, `Severity`), `web/src/components/ui/ErrorState`.
- Next 16: `params` is a `Promise<{ id: string }>`; `notFound()` from `next/navigation` renders
  the sibling `not-found.tsx` (verified at briefing time from the v16.3.4 `page.js` reference).

## Files

- Create: `web/src/app/alerts/[id]/page.tsx`, `web/src/app/alerts/[id]/not-found.tsx`,
  `web/src/components/alerts/AlertHeader/{AlertHeader.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/VerdictPanel/{VerdictPanel.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/RoutingInfo/{RoutingInfo.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/RawJson/{RawJson.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/Timeline/{Timeline.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/StatusBanner/{StatusBanner.tsx,interface.ts,index.ts}`
- Create (test-author, colocated): `AlertHeader.test.tsx`, `VerdictPanel.test.tsx`,
  `RoutingInfo.test.tsx`, `RawJson.test.tsx`, `Timeline.test.tsx`, `StatusBanner.test.tsx`
  (each beside its component)
- Modify: none

## Interfaces

- **Consumes:** `getJson`, `ApiError`, `AlertDetail`, `VerdictOut`, `ToolCallOut`,
  `AlertStatus`, `Badge`, `Severity`, `ErrorState`, `formatUtc`, `formatAge`, `formatUsd`,
  `formatPercent`, `formatLatency`, `formatTokens`; `next/link`, `next/navigation` (`notFound`).
- **Produces (M4 relies on `Timeline`'s props — produce exactly):**

  ```ts
  // web/src/components/alerts/AlertHeader/interface.ts
  export type AlertHeaderProps = { alert: AlertDetail; now: Date };
  // AlertHeader.tsx: <header> with <h1 class="font-mono">{alert.src_ip}</h1> and a <dl>: Sensor {sensor} · Source {source} ·
  //   Status {status} · Event time <time dateTime={event_time} title={formatUtc(event_time)}>{formatUtc(event_time)}</time> ·
  //   Received <time dateTime={received_at} title={formatUtc(received_at)}>{formatUtc(received_at)} ({formatAge(received_at, now)})</time>

  // web/src/components/alerts/VerdictPanel/interface.ts
  export type VerdictPanelProps = { verdict: VerdictOut };
  // VerdictPanel.tsx: <section aria-labelledby="verdict-heading"> <h2 id="verdict-heading">Verdict</h2>
  //   <Badge severity={verdict.severity as Severity} /> <Badge category={verdict.category} />
  //   <span>Confidence {formatPercent(verdict.confidence)}</span> <span>{verdict.escalate ? "Escalate: yes" : "Escalate: no"}</span>
  //   <h3>Reasoning</h3> <p class="whitespace-pre-wrap">{verdict.reasoning}</p>
  //   <h3>Recommended action</h3> <p class="whitespace-pre-wrap">{verdict.recommended_action}</p>
  //   — JSX text nodes only; no markdown rendering, no dangerouslySetInnerHTML (PRD §10.6; spine constraint)

  // web/src/components/alerts/RoutingInfo/interface.ts
  export type RoutingInfoProps = { verdict: VerdictOut };
  // RoutingInfo.tsx: <section aria-labelledby="routing-heading"><h2 id="routing-heading">Routing</h2><dl class="tabular-nums"> with exactly nine dt/dd pairs, in this order:
  //   "Primary model" {model_primary} · "Final model" {model_final} · "Escalated to strong model" {escalated_model ? "yes" : "no"} ·
  //   "Prompt version" {prompt_version} · "Input tokens" {formatTokens(input_tokens)} · "Output tokens" {formatTokens(output_tokens)} ·
  //   "Cost" {formatUsd(verdict.cost_usd)} · "Latency" {formatLatency(latency_ms)} · "Verdict at" {formatUtc(created_at)}
  //   (cost_usd arrives as the API's decimal string — see task-01; formatUsd handles string | null)

  // web/src/components/alerts/RawJson/interface.ts
  export type RawJsonProps = { raw: Record<string, unknown> };
  // RawJson.tsx: <details><summary>Raw session JSON</summary><pre class="overflow-x-auto font-mono text-xs">{JSON.stringify(raw, null, 2)}</pre></details>
  //   — React escapes the string; a <script> inside a username is text, never an element

  // web/src/components/alerts/Timeline/interface.ts
  export type TimelineProps = { toolCalls: readonly ToolCallOut[] };
  // Timeline.tsx: <ol aria-label="Tool trace"> —
  //   toolCalls.length === 0 -> exactly one <li>Tool trace arrives at M4</li>
  //   else one <li key={seq}> per call, in the given order, text `${seq}. ${tool_name} · ${formatLatency(latency_ms)}`
  //   (M4 replaces the item body with tool, arguments, result, latency — FRONTEND-CONVENTIONS §4; the <ol> and aria-label stay)

  // web/src/components/alerts/StatusBanner/interface.ts
  export type StatusBannerProps = { status: "pending" | "failed" };
  // StatusBanner.tsx: <p role="status"> "Triage pending — no verdict yet." | "Triage failed — no verdict was produced."

  // web/src/app/alerts/[id]/page.tsx — React Server Component
  export const dynamic = "force-dynamic";
  export default async function AlertDetailPage({ params }: { params: Promise<{ id: string }> }): Promise<JSX.Element>;
    // const { id } = await params;
    // try { alert = await getJson<AlertDetail>(`/api/v1/alerts/${encodeURIComponent(id)}`); }
    // catch (e) {
    //   if (e instanceof ApiError && (e.status === 404 || e.status === 422)) notFound();     // unknown id, or not a UUID
    //   const err = e instanceof Error ? e : new Error(String(e));
    //   return <ErrorState title={e instanceof ApiError ? `API error ${e.status}` : "API unreachable"} detail={err.message} />;
    // }
    // <article> <Link href="/alerts">← Alert queue</Link> <AlertHeader alert={alert} now={new Date()} />
    //   {alert.verdict ? <><VerdictPanel verdict={alert.verdict} /><RoutingInfo verdict={alert.verdict} /></>
    //                  : <StatusBanner status={alert.status === "failed" ? "failed" : "pending"} />}
    //   <Timeline toolCalls={alert.tool_calls} /> <RawJson raw={alert.raw} /> </article>
  // web/src/app/alerts/[id]/not-found.tsx: <section><h1>Alert not found</h1><p>No alert has that id.</p><Link href="/alerts">Back to the queue</Link></section>
  ```

## Interfaces → test table

Every component test file starts with `// @vitest-environment jsdom`; fixtures are complete
`VerdictOut` / `AlertDetail` / `ToolCallOut` literals so `tsc` checks them against the generated
types (`cost_usd` is a string such as `"0.000228"`).

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `AlertHeader` identity fields | `web/src/components/alerts/AlertHeader/AlertHeader.test.tsx` — `it("renders source ip, sensor, source and status")` | `getByRole("heading", { level: 1 })` is the IP; sensor/source/status text present |
| `AlertHeader` times | `AlertHeader.test.tsx` — `it("renders event and received times in UTC with a relative hint")` | two `<time>` elements with `title` ending `Z`; received shows `(3m ago)` |
| `VerdictPanel` text | `web/src/components/alerts/VerdictPanel/VerdictPanel.test.tsx` — `it("renders the full reasoning and recommended action as text")` | 400-char reasoning fully present (no excerpting); action present |
| reasoning escaped | `VerdictPanel.test.tsx` — `it("escapes a <script> fragment in reasoning")` | `getByText(/<script>alert\(1\)<\/script>/)` and `container.querySelector("script") === null` |
| action escaped | `VerdictPanel.test.tsx` — `it("escapes a <script> fragment in recommended_action")` | same pin on `recommended_action` |
| confidence + escalate | `VerdictPanel.test.tsx` — `it("renders confidence as a whole percent and the escalate flag as text")` | `0.9` → `Confidence 90%`; `Escalate: yes` / `Escalate: no` |
| badges | `VerdictPanel.test.tsx` — `it("renders the severity and category badges")` | `getByLabelText("Severity 4")`, `getByText("successful_intrusion")` |
| `RoutingInfo` pairs | `web/src/components/alerts/RoutingInfo/RoutingInfo.test.tsx` — `it("renders the nine routing fields in a definition list")` | `container.querySelectorAll("dt").length === 9`; model names, prompt version present |
| cost / latency formatting | `RoutingInfo.test.tsx` — `it("formats cost from the decimal string and latency with a unit")` | `"0.000228"` → `$0.000228`; `1234` → `1,234 ms`; tokens `1,000` |
| nullable fields | `RoutingInfo.test.tsx` — `it("renders escalated_model as yes/no and null tokens, cost and latency as —")` | `escalated_model: true` → `yes`; nulls → `—` (three occurrences) |
| `RawJson` collapsed | `web/src/components/alerts/RawJson/RawJson.test.tsx` — `it("renders a collapsed details element with the summary text")` | `details` without `open`; `getByText("Raw session JSON")`; `<pre>` contains `"src_ip"` |
| raw escaped | `RawJson.test.tsx` — `it("escapes a <script> fragment inside a raw username as text")` | `raw.events[0].username = "<script>alert(1)</script>"` → text present, `container.querySelector("script") === null` |
| `Timeline` placeholder | `web/src/components/alerts/Timeline/Timeline.test.tsx` — `it("renders the M4 placeholder when there are no tool calls")` | `getByRole("list", { name: "Tool trace" })`; exactly one `listitem` with text `Tool trace arrives at M4` |
| `Timeline` items | `Timeline.test.tsx` — `it("renders one item per tool call in the given order")` | seq 0 `lookup_ip_reputation`, seq 1 `get_session_commands` → two `listitem`s, in order, no placeholder |
| `StatusBanner` pending | `web/src/components/alerts/StatusBanner/StatusBanner.test.tsx` — `it("explains a pending alert")` | `getByRole("status")` text `Triage pending — no verdict yet.` |
| `StatusBanner` failed | `StatusBanner.test.tsx` — `it("explains a failed alert")` | `Triage failed — no verdict was produced.` |
| `page.tsx` 404 → `notFound()`, `not-found.tsx` | no unit test (async RSC); task-07's acceptance walk: `curl -o /dev/null -w "%{http_code}" localhost:3000/alerts/$(uuidgen)` → `404`, and `/alerts/not-a-uuid` → `404` | — |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the six test files; the **implementer** does
Steps 3–5 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the six test files** per the table; format with
  `pnpm -C web format`, check `pnpm -C web lint`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `pnpm -C web test` → Expected: the six
  new files fail with `Failed to resolve import` for, respectively,
  `@/components/alerts/AlertHeader`, `@/components/alerts/VerdictPanel`,
  `@/components/alerts/RoutingInfo`, `@/components/alerts/RawJson`,
  `@/components/alerts/Timeline`, `@/components/alerts/StatusBanner`; the 11 earlier files stay
  green (`Test Files 6 failed | 12 passed`). Pin, commit
  `test(web): detail page components incl. three escape pins RED (m3 task-05)`.
- [ ] **Step 3 (GREEN — implementer): the six components** (folder-per-component, barrels).
  `pnpm -C web type-check` clean; `pnpm -C web test` → `Test Files 18 passed (18) / Tests 55
  passed (55)`.
- [ ] **Step 4 (GREEN — implementer): `src/app/alerts/[id]/page.tsx` and `not-found.tsx`.**
  `type-check` clean.
- [ ] **Step 5 (implementer): render against the real API once** — with the task-04 dev setup,
  open `/alerts/<id>` for a triaged alert (verdict + routing + raw JSON), for a `failed` alert
  (banner), and `/alerts/<random uuid>` (404 page); paste `curl -s -o /dev/null -w "%{http_code}\n"
  localhost:3000/alerts/$(uuidgen)` → `404` into the report. Then all gates (Python five + the
  four web gates) → commit:
  `feat(web): /alerts/[id] detail page with verdict, routing, raw JSON, timeline placeholder (m3 task-05)`
  with the two trailers; path-scoped `git add web/src`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test   # Test Files 18 passed (18) / Tests 59 passed (59)
grep -rn "dangerouslySetInnerHTML\|use client" web/src ; echo "exit=$?"                        # exit=1
grep -c 'aria-label="Tool trace"' web/src/components/alerts/Timeline/Timeline.tsx               # 1
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean
```

## Acceptance

- `/alerts/[id]` shows the full reasoning, recommended action, confidence `NN%`, escalate flag,
  routing info, cost `$0.000228`-style and latency, a collapsible raw JSON block and the
  `Tool trace` `<ol>` placeholder; `pending`/`failed` alerts show the status banner instead of a
  verdict; unknown ids render the not-found page.
- The three escape pins (reasoning, recommended action, raw JSON) pass: a `<script>` fragment is
  visible text and never an element.
