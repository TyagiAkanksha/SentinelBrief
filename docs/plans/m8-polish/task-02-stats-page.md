---
id: task-02
milestone: m8-polish
depends_on: [task-01]
status: planned
spec: PRD.md §9 page 3 ("/stats — volume over time, severity distribution, cost/alert trend, p95 latency, escalation rate"), §8 (`GET /api/v1/stats` cached 60 s; one envelope; stable `operation_id`; codegen from the committed baseline), §9 design ("dark, dense, legible"; "nothing on any page triggers compute"; severity never colour-only); `docs/FRONTEND-CONVENTIONS.md` §2–§9; `.claude/rules/{api,core,web,tests}.md`; CONVENTIONS.md §3, §8
---

# task-02 — `/stats`: the cost-per-day series on `StatsOut`, and the dashboard's third page

## Goal

PRD §9's third page exists and is honest about the numbers. `GET /api/v1/stats` already reports
volume by day, the severity and category distributions, the escalated count, total and mean cost,
and p50/p95 latency; the one thing the page needs that the API cannot answer today is the
**cost-per-alert trend**, so `StatsOut` gains a `cost_by_day` series computed in the same read
service. The page itself is a React Server Component that fetches once through the existing
server-side fetcher and renders: a row of headline stats (total alerts, triaged, escalation rate,
mean cost per alert, p50/p95 latency, last alert), then alerts per day, the severity distribution,
the category distribution, and the cost trend. Every derivation the page shows — percentages, bar
widths, the escalation rate — is a pure function in `web/src/lib/stats.ts` with unit tests; the
components only render what they are handed.

Nothing on this page triggers compute (PRD §9): it is one cached `GET` and nothing else.

## Context (read ONLY these)

- `PRD.md` §8 (the `/stats` row), §9 (page 3 and the Design paragraph).
- `docs/plans/m8-polish.md` — Goal and **Global Constraints** (all of them, including the M8a
  section).
- `docs/FRONTEND-CONVENTIONS.md` §2 (Tailwind tokens live only in `globals.css`), §3
  (folder-per-component; dumb components; logic in hooks or server components), §4 (primitives),
  §5 (one codegen boundary), §6 (RSC pages fetch through `API_URL`), §7 (Vitest), §9
  (accessibility: tables carry a `<caption>`; never colour-only).
- `CONVENTIONS.md` §3 (services are session-first), §8 (baseline + codegen in one commit), §9, §10.
- `.claude/rules/api.md`, `.claude/rules/core.md`, `.claude/rules/web.md`, `.claude/rules/tests.md`.
- Code you build on:
  - `core/schemas/alerts_read.py` (`StatsOut`, `DayVolume` — the model `DayCost` sits beside)
  - `core/services/alerts_read.py::get_stats` (the existing query set, `_SIX_DP`,
    `latest_verdicts_subquery`) and `core/models/{alerts,verdicts}.py`
  - `api/routes/alerts_read.py` (the `/stats` route and its 60 s cache — unchanged by this task)
  - `web/src/lib/api/server.ts` (`getJson`, `ApiError`), `web/src/lib/format.ts` (+ its test),
    `web/src/app/alerts/page.tsx` (the RSC fetch-and-render shape, including the try/catch that
    turns a failure into an error state), `web/src/app/layout.tsx` (the nav),
    `web/src/components/ui/{EmptyState,ErrorState,DataTable,Badge}/` (existing primitives and the
    folder shape to copy), `web/src/types/api.ts`
  - `tests/test_alerts_read_service.py`, `tests/test_read_routes.py`, `tests/test_read_schemas.py`,
    `tests/helpers.py` (`seed_alert`, `add_verdict` — the only way tests create rows)

## Files

- Modify: `core/schemas/alerts_read.py` (`DayCost`; `StatsOut.cost_by_day`),
  `core/services/alerts_read.py` (`get_stats` computes it), `api/openapi.json` (regenerated),
  `web/src/types/generated/schema.d.ts` (regenerated), `web/src/types/api.ts` (`DayCost` alias),
  `web/src/lib/format.ts` (`formatCount`), `web/src/app/layout.tsx` (nav gains Stats)
- Create (web): `web/src/lib/stats.ts`, `web/src/app/stats/page.tsx`,
  `web/src/components/ui/Stat/{Stat.tsx,interface.ts,index.ts}`,
  `web/src/components/stats/DistributionTable/{DistributionTable.tsx,interface.ts,index.ts}`,
  `web/src/components/stats/CostTable/{CostTable.tsx,interface.ts,index.ts}`
- Create (test-author — the pinned files): `tests/test_stats_cost_by_day.py`,
  `web/src/lib/stats.test.ts`, `web/src/components/ui/Stat/Stat.test.tsx`,
  `web/src/components/stats/DistributionTable/DistributionTable.test.tsx`,
  `web/src/components/stats/CostTable/CostTable.test.tsx`, `web/src/app/stats/page.test.tsx`
- Modify (test-author): `web/src/lib/format.test.ts` (the `formatCount` cases — ruling R11); and,
  only if they enumerate an exact field set that `cost_by_day` breaks, `tests/test_read_schemas.py`
  and `tests/test_read_routes.py`. Check those two first; if no change is needed, say so in the report.

## Interfaces

- **Consumes (exists today):**

  ```python
  # core/schemas/alerts_read.py
  class DayVolume(BaseModel): day: date; count: int
  class StatsOut(BaseModel):
      total_alerts: int; by_status: dict[str, int]; by_severity: dict[str, int]
      by_category: dict[str, int]; escalated_count: int; volume_by_day: list[DayVolume]
      cost_total_usd: Decimal; cost_mean_usd: Decimal
      latency_p50_ms: int; latency_p95_ms: int; last_alert_at: datetime | None
  # core/services/alerts_read.py::get_stats(session) -> StatsOut   (zero-filled on an empty database)
  #   _SIX_DP + ROUND_HALF_UP is how cost_mean_usd is already quantized — cost_by_day matches it.
  ```

  ```ts
  // web/src/lib/format.ts (existing): formatUtc, formatAge, formatUsd, formatPercent,
  //   formatLatency, formatTokens, countryFlag, formatDatetimeLocalUtc
  // web/src/lib/api/server.ts (existing): getJson<T>(path), ApiError { status, envelope }
  ```

- **Produces (exactly):**

  ```python
  # core/schemas/alerts_read.py
  class DayCost(BaseModel):
      """One UTC day's spend, keyed on the day the ALERTS were received."""
      day: date
      alerts: int            # alerts received that day
      cost_usd: Decimal      # every verdict of those alerts, retriage spend included
      mean_cost_usd: Decimal # cost_usd / alerts, quantized to six places, ROUND_HALF_UP

  class StatsOut(BaseModel):
      ...                    # every existing field stays, in its existing order
      cost_by_day: list[DayCost]   # ascending by day; [] on an empty database

  # core/services/alerts_read.py::get_stats — one added query, ascending by day:
  #   SELECT date(timezone('UTC', alerts.received_at)) AS day,
  #          count(DISTINCT alerts.id)                 AS alerts,
  #          coalesce(sum(verdicts.cost_usd), 0)       AS cost_usd
  #   FROM alerts LEFT JOIN verdicts ON verdicts.alert_id = alerts.id
  #   GROUP BY day ORDER BY day
  # count(DISTINCT alerts.id) is load-bearing: the LEFT JOIN multiplies an alert's row by its
  # verdict count, so a retriaged alert would otherwise be counted twice in `alerts` and drag the
  # mean down. mean_cost_usd = (cost_usd / alerts) when alerts > 0 else Decimal("0"), quantized
  # with _SIX_DP and ROUND_HALF_UP — the same treatment cost_mean_usd already gets.
  ```

  ```ts
  // web/src/types/api.ts — one added alias
  export type DayCost = components["schemas"]["DayCost"];

  // web/src/lib/format.ts — one added export; formatTokens delegates to it so there is ONE impl
  export function formatCount(n: number | null): string;   // thousands separators; "—" for null/non-finite
  export function formatTokens(n: number | null): string { return formatCount(n); }

  // web/src/lib/stats.ts — every derivation the page shows, as pure functions
  export type DistributionRow = {
    key: string;      // the raw bucket key ("4", "brute_force", "2026-09-12")
    label: string;    // what the table prints ("Severity 4", "Brute force", "2026-09-12")
    count: number;
    share: number;    // count / total, 0..1 (0 when total is 0) — printed with formatPercent
    bar: number;      // Math.round((count / max) * 100), 0..100 (0 when max is 0) — the bar's width %
  };
  export function humanizeCategory(key: string): string;      // "brute_force" -> "Brute force"; unknown keys pass through with _ -> space and an initial capital
  export function triagedCount(stats: StatsOut): number;      // stats.by_status.triaged ?? 0
  export function escalationRate(stats: StatsOut): number;    // escalated_count / triagedCount, 0 when triaged is 0
  export function severityRows(stats: StatsOut): DistributionRow[];   // keys "1".."5" ALWAYS, ascending, label `Severity ${k}`, missing key -> 0
  export function categoryRows(stats: StatsOut): DistributionRow[];   // every key of by_category, count desc then key asc; zero-count categories are dropped
  export function volumeRows(stats: StatsOut): DistributionRow[];     // volume_by_day in API order, label = day
  // share/bar denominators: `share` uses the sum of that series' counts; `bar` uses its largest count.

  // web/src/components/ui/Stat/interface.ts
  export type StatProps = { label: string; value: string; hint?: string };
  // Stat.tsx: a dumb card — <div> with the label as a <dt>-styled span, the value large and
  // tabular, the optional hint muted underneath. No logic, no formatting, no data access.

  // web/src/components/stats/DistributionTable/interface.ts
  export type DistributionTableProps = { caption: string; labelHeader: string; rows: DistributionRow[]; emptyMessage: string };
  // DistributionTable.tsx: a <table> with the required <caption> and the columns
  //   {labelHeader} | Count | Share — plus a bar rendered as a token-coloured <div> whose inline
  //   width is `${row.bar}%` inside the Share cell. The bar is aria-hidden; the numbers carry the
  //   meaning (FRONTEND-CONVENTIONS §9 — never colour-only). Renders `emptyMessage` in a single
  //   full-width cell when rows is empty. Used three times: severity, category, volume.

  // web/src/components/stats/CostTable/interface.ts
  export type CostTableProps = { rows: DayCost[]; emptyMessage: string };
  // CostTable.tsx: <table> with caption "Cost per day" and columns Day | Alerts | Total | Mean per
  // alert, formatted with formatCount and formatUsd. Newest day first (reverse of the API order —
  // the component does the reversal, it is presentation, and the test pins it).

  // web/src/app/stats/page.tsx — a Server Component, same shape as /alerts
  export const dynamic = "force-dynamic";
  export const metadata = { title: "Stats — SentinelBrief" };
  // getJson<StatsOut>("/api/v1/stats") inside try/catch -> ErrorState on failure (ApiError renders
  // `API error ${status}` + the envelope message, anything else "API unreachable"), EmptyState
  // ("No alerts yet — run scripts/seed_dev.py") when total_alerts === 0, otherwise:
  //   <h1>Stats</h1>
  //   six <Stat> cards: Total alerts (formatCount) · Triaged (formatCount) · Escalation rate
  //     (formatPercent, hint `${escalated_count} of ${triaged} triaged`) · Mean cost per alert
  //     (formatUsd, hint `Total ${formatUsd(cost_total_usd)}`) · Latency p50 / p95 (formatLatency,
  //     two cards) · Last alert (formatUtc or "—")
  //   <DistributionTable> alerts per day · severity · category
  //   <CostTable>
  // web/src/app/layout.tsx: the Primary nav gains <Link href="/stats">Stats</Link> after Alerts.
  ```

## Briefing rulings (decided)

- **R5 — `cost_by_day` is keyed on the alert's received day, not the verdict's creation day.** A
  retriage weeks later belongs to the day the traffic arrived; that is the series a reader compares
  against `volume_by_day`. Both series therefore share an x-axis. Cost if wrong: one query's
  `GROUP BY` expression.
- **R6 — no chart library.** Bars are token-coloured divs inside accessible tables with captions.
  PRD §4's stack is fixed and this milestone adds no dependency. Cost if wrong: a prettier chart
  later, behind a dependency decision the owner has not made.
- **R7 — the page adds no API call beyond the existing cached `/stats`.** Nothing here computes
  (PRD §9/§10.1).
- **R11 — `formatCount`'s tests go into the existing `web/src/lib/format.test.ts`, written by the
  test-author.** `formatCount` is an export of `format.ts` and this repo colocates one test file per
  module. That file is not pinned for this task; having the test-author extend it in the RED commit
  keeps the implementer away from authored test files entirely. Cost if wrong: a few assertions
  move.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `cost_by_day` basic | `tests/test_stats_cost_by_day.py::test_cost_by_day_sums_verdict_cost_per_received_day` (DB fixtures) | two alerts on day A (0.000100 + 0.000200) and one on day B (0.000050) via `seed_alert`/`add_verdict` → two rows, ascending, exact `Decimal` totals |
| **retriage double-count (load-bearing)** | `::test_cost_by_day_counts_an_alert_once_when_it_has_two_verdicts` | one alert, two verdicts → `alerts == 1`, `cost_usd` is the sum of both, `mean_cost_usd == cost_usd`. Mutating `count(DISTINCT alerts.id)` to `count(alerts.id)` must fail this test |
| mean quantization | `::test_cost_by_day_mean_is_quantized_to_six_places_half_up` | three alerts, total `0.000100` → mean `Decimal("0.000033")`; assert the exponent is `-6` |
| pending alerts | `::test_cost_by_day_counts_alerts_with_no_verdict_at_zero_cost` | a `pending` alert alone on its day → `alerts == 1`, `cost_usd == Decimal("0")`, `mean_cost_usd == Decimal("0")` (the LEFT JOIN branch) |
| empty database | `::test_cost_by_day_is_empty_on_an_empty_database` | `get_stats` → `cost_by_day == []` and every existing field still zero-filled |
| wire surface | `::test_stats_response_carries_cost_by_day` (route, via `httpx` + `ASGITransport`) | `GET /api/v1/stats` JSON has `cost_by_day` with the keys `day`, `alerts`, `cost_usd`, `mean_cost_usd`; `cost_usd` is a JSON **string** (the `Decimal` convention the rest of the API already uses) |
| `humanizeCategory` | `web/src/lib/stats.test.ts` — `it("humanizes every VerdictCategory key")` | all seven PRD categories; an unknown key `"weird_thing"` → `"Weird thing"` |
| `triagedCount` / `escalationRate` | `it("computes the escalation rate over triaged alerts and returns 0 with none")` | 3 of 12 triaged → 0.25; `by_status` without `triaged` → 0 (the `noUncheckedIndexedAccess` branch); no division by zero |
| `severityRows` | `it("always returns five severity rows in ascending order, zero-filling gaps")` | `by_severity` `{"1": 3, "4": 1}` → five rows; labels `Severity 1`…`Severity 5`; `share` of row 1 is 0.75; `bar` of row 1 is 100 and of row 4 is 33 |
| `categoryRows` | `it("orders categories by count desc then key asc and drops empty buckets")` | a tie broken by key; a zero-count category absent |
| `volumeRows` | `it("keeps the API's day order and labels each row with its day")` | order preserved; `bar` relative to the busiest day |
| zero denominators | `it("returns share 0 and bar 0 for an all-zero series")` | every count 0 → no `NaN` anywhere (`Number.isFinite` on every `share` and `bar`) |
| `formatCount` | existing `web/src/lib/format.test.ts` (extended by the test-author, ruling R11) — `it("formatCount separates thousands and dashes missing values")` | `1234` → `"1,234"`; `null`, `NaN`, `Infinity` → `"—"`; the existing `formatTokens` cases stay green |
| `Stat` | `web/src/components/ui/Stat/Stat.test.tsx` — `it("renders the label, the value and the optional hint")` | the hint element is absent when `hint` is undefined |
| `DistributionTable` | `.../DistributionTable.test.tsx` — `it("renders a caption, one row per bucket and the share as text")` | `screen.getByRole("table")` has an accessible name from the caption; the counts and percents are text; the bar div is `aria-hidden` |
| `DistributionTable` empty | `it("renders the empty message when there are no rows")` | `emptyMessage` visible, no data rows |
| `CostTable` | `.../CostTable.test.tsx` — `it("renders days newest first with formatted costs")` | two days in API (ascending) order in → the newest renders first; `$0.000228`-shaped cells |
| `/stats` page happy path | `web/src/app/stats/page.test.tsx` — `it("renders the headline stats and all four tables")` (`vi.mock("@/lib/api/server")`) | the six card labels and the four captions are present |
| `/stats` page empty | `it("renders the empty state when the database has no alerts")` | `total_alerts: 0` → empty message, no tables |
| `/stats` page error | `it("renders the API error envelope message when the fetch fails")` | `getJson` rejects with `new ApiError(503, {error:{code:"internal_error",message:"db down"}})` → `role="alert"` carries both |
| nav | `it("links to Stats from the primary nav")` (in the page test file or a layout test) | `/stats` link present |

## Steps (TDD)

- [ ] **Step 1 (RED — test-author): write the six test files** per the table. The Python test file
  seeds only through `tests/helpers.py::seed_alert` / `add_verdict` and asserts `Decimal` values with
  `Decimal("...")`, never floats. Frontend tests import `describe/it/expect/vi` explicitly, use
  `// @vitest-environment jsdom` on line 1 of every component/page test, and mock only
  `@/lib/api/server`. Prettier-format the TS files before committing.
- [ ] **Step 2 (RED — test-author): prove they fail.**
  `export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0`
  `uv run pytest -q -rs tests/test_stats_cost_by_day.py` → Expected: `AttributeError`/`ImportError`
  on `DayCost` (or assertion failures naming `cost_by_day`), 0 skipped. `pnpm -C web test` →
  Expected: unresolved imports for `@/lib/stats`, `@/components/ui/Stat`,
  `@/components/stats/DistributionTable`, `@/components/stats/CostTable`, `@/app/stats/page`.
  Paste both, sha256 every authored file, commit
  `test(core,web): cost-by-day series and the stats page RED (m8a task-02)` with the two trailers.
- [ ] **Step 3 (GREEN — implementer): `DayCost` + `StatsOut.cost_by_day` + the `get_stats` query.**
  `uv run mypy --no-incremental`, then
  `uv run pytest -q -rs tests/test_stats_cost_by_day.py tests/test_alerts_read_service.py tests/test_read_routes.py tests/test_read_schemas.py` → all pass.
- [ ] **Step 4 (GREEN — implementer): regenerate the wire surface in this same commit.**
  `uv run python scripts/export_openapi.py` and `pnpm -C web codegen`; add the `DayCost` alias to
  `web/src/types/api.ts`.
- [ ] **Step 5 (GREEN — implementer): `web/src/lib/stats.ts` and `formatCount`.**
  `pnpm -C web test src/lib` → the pure tests pass. Run `pnpm -C web type-check` before moving on.
- [ ] **Step 6 (GREEN — implementer): the three components, then the page and the nav link.**
  Folder-per-component with `interface.ts` and an `index.ts` barrel. No hex colours — use the
  `--color-*` tokens from `globals.css` (`bg-sev-4`, `text-muted`, `border-border`, …).
  `pnpm -C web test` → all pass.
- [ ] **Step 7 (implementer): see it.** With the dev stack up and `uv run python scripts/seed_dev.py`
  run, `pnpm -C web dev` and open `/stats`; paste
  `curl -s http://127.0.0.1:8000/api/v1/stats | python3 -m json.tool | head -40` into the report,
  and state in one sentence what the page showed. If the dev stack is unavailable, say so
  explicitly and paste the page test's output instead.
- [ ] **Step 8 (implementer): full gates, then one commit.**
  ```bash
  export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
  uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
  pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
  ```
  0 skipped. Path-scoped `git add`, message
  `feat(core,web): per-day cost series and the /stats dashboard page (m8a task-02)` plus the two
  trailers. Re-verify and report the six pinned sha256 values.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_stats_cost_by_day.py tests/test_alerts_read_service.py tests/test_read_routes.py tests/test_read_schemas.py tests/test_openapi_baseline.py tests/test_openapi_hygiene.py   # all pass, 0 skipped
uv run python scripts/export_openapi.py && git diff --exit-code -- api/openapi.json && echo no-drift
pnpm -C web codegen && git diff --exit-code -- web/src/types/generated && echo no-drift
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
grep -rn "#[0-9a-fA-F]\{6\}" web/src --include='*.tsx' --include='*.ts'; echo "exit=$?"   # exit=1
grep -c "caption" web/src/components/stats/DistributionTable/DistributionTable.tsx web/src/components/stats/CostTable/CostTable.tsx   # >= 1 each
```

## Acceptance

- `GET /api/v1/stats` carries `cost_by_day`, one row per UTC day of received alerts, counting each
  alert once however many verdicts it has, with a six-place mean.
- `/stats` renders the headline stats and the four tables from a single cached fetch, shows an
  honest empty state on an empty database and the enveloped error message on a failure, and every
  number it prints comes from a unit-tested pure function.
- Tables carry captions, bars are `aria-hidden` decoration over text values, no hex colour appears
  outside `globals.css`, and both baselines (OpenAPI and generated types) moved in this commit.
