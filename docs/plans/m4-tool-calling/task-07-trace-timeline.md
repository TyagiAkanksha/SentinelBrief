---
id: task-07
milestone: m4-tool-calling
depends_on: [task-06]
status: planned
spec: PRD.md §9 (page 1: source IP + country flag; page 2: "tool-call trace rendered as a timeline (tool, args, result, latency — this is what interviewers will inspect)"), §8 (detail = alert + latest verdict + full tool trace; a DTO change regenerates `api/openapi.json` in the same commit), §10.6 (`arguments`/`result` are attacker-influenced JSON — escaped text), §12 M4 ("the trace renders"); docs/FRONTEND-CONVENTIONS.md §3 (dumb components), §4 (`Timeline` is an `<ol>`, faithful not decorative), §5 (one codegen boundary), §7, §9 (never color-only; timeline is an `<ol>`); `.claude/rules/web.md`; M3 task-05 carry-over (`TimelineProps = { toolCalls: readonly ToolCallOut[] }` and `<ol aria-label="Tool trace">` stay); M3 ruling Q6 (country flag lands with `get_ip_geo_asn`)
---

# task-07 — Trace timeline on `/alerts/[id]` (tool, arguments, result, latency; escaped), `country` on the alert DTOs from the geo tool result, country flag on the queue and detail IP cells, M4 acceptance walk

## Goal

The interview artifact: `Timeline` renders each persisted tool call as one `<li>` of the existing
`<ol aria-label="Tool trace">` — sequence number and tool name, the arguments and the result as
pretty-printed JSON in `<pre>` blocks (React-escaped text: a `<script>` inside a command is
characters, never an element), the latency via `formatLatency`, and a visible `unavailable
(<reason>)` marker when the tool degraded — with the empty state "No tool calls were made."
replacing the M3 placeholder. The queue and the detail header show a country flag next to the IP
(PRD §9, deferred from M3): `AlertBase` gains `country: str | None = None`, filled from the latest
verdict's first `get_ip_geo_asn` result (`result->>'country'` in SQL for the list; from the
detail's own `tool_calls` in Python) and normalized to `^[A-Z]{2}$` or `null`; the web renders
the regional-indicator emoji with `role="img"` and an `aria-label` of `Country XX`, and renders
nothing when the country is `null` — an `{unavailable}` geo result therefore shows no flag. The
DTO change regenerates `api/openapi.json` and the web codegen in the same commit. The task ends
with the controller's M4 acceptance walk.

## Context (read ONLY these)

- `PRD.md` §8, §9, §10.6, §12 M4.
- `docs/plans/m4-tool-calling.md` — Global Constraints (country flag: escaped text + emoji,
  `{unavailable}` → no flag) and the Acceptance walk table.
- `docs/FRONTEND-CONVENTIONS.md` §3, §4, §5, §7, §9; `.claude/rules/web.md`; `CONVENTIONS.md`
  §8 (OpenAPI baseline regenerated in the same commit).
- Task-03 output: the geo result shape (`country` is the DB's `iso_code`, 2 uppercase ASCII
  letters, or `null`). Task-06 output: `tool_calls` rows now exist; the seed writes traces for
  the five fixture alerts (`alert4` → `get_session_commands` then `get_ip_geo_asn` → `DE`;
  `alert1` → `NL`; `alert2` → `US`; `alert3` → `SG`; `alert5` → `BR`).
- Code you build on: `core/schemas/alerts_read.py` (`AlertBase`, `AlertSummary`, `AlertDetail`,
  `ToolCallOut`), `core/services/alerts_read.py` (`list_alerts` — the `base` select and the
  `latest` subquery; `get_alert_detail`), `core/models/tool_calls.py` (`ToolCallRow.result` JSONB),
  `tests/test_read_schemas.py::test_alert_detail_and_summary_share_base_fields` (still true: the
  field sets differ by exactly `{raw, tool_calls}` and the verdict type),
  `tests/test_alerts_read_service.py` (`_BARE_RAW_COLUMN`, seeding style), `tests/helpers.py`
  (`seed_alert(..., tool_calls=…)`, `add_verdict`), `scripts/export_openapi.py`,
  `web/src/components/alerts/Timeline/{Timeline.tsx,interface.ts,Timeline.test.tsx}`,
  `web/src/components/alerts/AlertRow/{AlertRow.tsx,AlertRow.test.tsx}` (the six-cell pin — the
  flag goes *inside* the IP cell), `web/src/components/alerts/AlertHeader/{AlertHeader.tsx,
  AlertHeader.test.tsx}`, `web/src/components/alerts/RawJson/RawJson.tsx` (the `<pre>` + JSON
  pattern), `web/src/components/ui/Badge/` (folder-per-component + `aria-label` pattern),
  `web/src/lib/format.ts` + `format.test.ts`, `web/src/types/api.ts`, `web/src/app/alerts/[id]/
  page.tsx`, `README.md` (status line), `docs/plans/m3-read-path-dashboard/task-07-web-image-
  compose-acceptance.md` Step 7 (the acceptance-walk shape; plan defect 5: never `grep -c` on
  SSR output — use `grep -o … | sort -u`).

## Files

- Create: `web/src/components/ui/CountryFlag/{CountryFlag.tsx,interface.ts,index.ts}`
- Create (test-author): `tests/test_country_field.py`,
  `web/src/components/ui/CountryFlag/CountryFlag.test.tsx`
- Modify (test-author, re-pinned): `web/src/components/alerts/Timeline/Timeline.test.tsx`
  (placeholder text + item bodies), `web/src/components/alerts/AlertRow/AlertRow.test.tsx`
  (flag cases), `web/src/components/alerts/AlertHeader/AlertHeader.test.tsx` (flag cases),
  `web/src/lib/format.test.ts` (`countryFlag`)
- Modify: `core/schemas/alerts_read.py`, `core/services/alerts_read.py`, `api/openapi.json`
  (regenerated), `web/src/types/generated/schema.d.ts` (regenerated), `web/src/lib/format.ts`,
  `web/src/components/alerts/Timeline/Timeline.tsx`,
  `web/src/components/alerts/AlertRow/AlertRow.tsx`,
  `web/src/components/alerts/AlertHeader/AlertHeader.tsx`, `README.md` (status line only, at the
  gate)

## Interfaces

- **Consumes:** `AlertBase`, `AlertSummary`, `AlertDetail`, `ToolCallOut`
  (`core.schemas.alerts_read`); `list_alerts`, `get_alert_detail`, `latest_verdicts_subquery`
  (`core.services.alerts_read`); `ToolCallRow`; `seed_alert`, `add_verdict`, `ToolCallRecord`
  (`tests.helpers`, `worker.outcome`); `formatLatency`, `Timeline`, `TimelineProps`,
  `AlertRowProps`, `AlertHeaderProps`, `Badge`; the generated `AlertSummary` / `AlertDetail` /
  `ToolCallOut` TS types.
- **Produces (M8's `/alerts` SSE rows and the M6 real-data pass rely on — produce exactly):**

  ```python
  # core/schemas/alerts_read.py
  COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")
  def normalize_country(value: object) -> str | None: ...        # value if isinstance(value, str) and COUNTRY_CODE_RE.match(value) else None
  class AlertBase(BaseModel):
      ...existing fields...
      country: str | None = None                                # ISO 3166-1 alpha-2 from the latest verdict's first get_ip_geo_asn result; None when
                                                                #   no such call, {unavailable}, or the value is not two uppercase letters.
                                                                #   Defaulted (not required) so every existing AlertSummary/AlertDetail literal in
                                                                #   the M3 web tests still type-checks; the API always sends the key.
  # OpenAPI: AlertSummary/AlertDetail gain `country: anyOf[string, null]`, not required; the TS type becomes `country?: string | null`.

  # core/services/alerts_read.py
  GEO_TOOL_NAME = "get_ip_geo_asn"
  def geo_country_subquery() -> Subquery: ...
      # select(ToolCallRow.verdict_id, ToolCallRow.result["country"].astext.label("country"))
      #   .where(ToolCallRow.tool_name == GEO_TOOL_NAME).distinct(ToolCallRow.verdict_id)
      #   .order_by(ToolCallRow.verdict_id, ToolCallRow.seq).subquery("geo")            # first geo call per verdict; ->> gives NULL for {unavailable}
  # list_alerts: base gains `.outerjoin(geo, geo.c.verdict_id == latest.c.id)` and projects geo.c.country; AlertSummary(country=normalize_country(row.country))
  #   — still no whole `alerts.raw` load (the I1 listener pin stays green)
  # get_alert_detail: country = normalize_country(next((tc.result.get("country") for tc in tool_calls if tc.tool_name == GEO_TOOL_NAME), None))
  #   (tool_calls are already loaded in seq order; no extra statement)
  ```

  ```ts
  // web/src/lib/format.ts
  export const COUNTRY_CODE_RE = /^[A-Z]{2}$/;
  export function countryFlag(code: string | null | undefined): string;
    // "" unless COUNTRY_CODE_RE.test(code); else the two regional-indicator code points
    // (String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65, 0x1f1e6 + c.charCodeAt(1) - 65)) — "DE" -> "🇩🇪"

  // web/src/components/ui/CountryFlag/interface.ts
  export type CountryFlagProps = { code: string | null | undefined };
  // CountryFlag behavior: renders nothing (null) when countryFlag(code) is ""; otherwise ONE element with role="img",
  //   aria-label `Country ${code}` and title = code whose text content is the emoji. It never renders the raw code as visible text
  //   (the aria-label carries it — FRONTEND-CONVENTIONS §9's "never color-only" analogue: the flag is never the only cue for assistive tech).

  // web/src/components/alerts/AlertRow — the IP cell now contains <CountryFlag code={alert.country} /> followed by the existing link;
  //   still exactly six cells (the M3 pin), the link's accessible name is still the bare IP.
  // web/src/components/alerts/AlertHeader — the <h1> contains the flag then the IP; the heading's text content still contains the IP.

  // web/src/components/alerts/Timeline — TimelineProps unchanged; <ol aria-label="Tool trace"> unchanged. Behavior per item (one <li key={seq}>,
  //   in the given order): a title line `${seq}. ${tool_name}` rendered as the item's first `<p>` (not an `<h*>` element — M4 task-07 review M9 ruling); the latency text formatLatency(latency_ms); a labelled block
  //   "Arguments" whose <pre> text is JSON.stringify(arguments, null, 2); a labelled block "Result" whose <pre> text is
  //   JSON.stringify(result, null, 2); when result.unavailable === true, additional visible text `unavailable (${result.reason})`
  //   (reason coerced with String(); "unknown" when absent). Empty list -> exactly one <li> with text "No tool calls were made."
  //   No markdown, no dangerouslySetInnerHTML, no client component.
  ```

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `normalize_country` | `tests/test_country_field.py::test_normalize_country_accepts_only_two_uppercase_letters` | `"DE"` → `"DE"`; `"de"`, `"DEU"`, `""`, `None`, `7`, `"D<"` → `None`; fails when lowercase is accepted |
| `AlertBase.country` default + shared | `tests/test_country_field.py::test_country_is_optional_on_summary_and_detail_and_base_fields_still_shared` | `AlertSummary(...)` without `country` validates to `None`; `test_alert_detail_and_summary_share_base_fields` in `tests/test_read_schemas.py` stays green; the OpenAPI schema for `AlertSummary` has `country` not in `required` |
| list country from the latest verdict's first geo call | `tests/test_country_field.py::test_list_alerts_country_comes_from_the_first_geo_call_of_the_latest_verdict` | alert with verdict v1 (geo `NL`) then v2 (`get_session_commands`, then geo `DE` at seq 1, then geo `FR` at seq 2) → `country == "DE"`; fails when the older verdict's or the later call's value wins |
| list: unavailable / no geo → null | `tests/test_country_field.py::test_list_alerts_country_is_null_for_unavailable_missing_or_malformed_geo` | geo `{"unavailable": true, "reason": "geoip_db_not_configured"}` → `None`; no geo call → `None`; geo `{"country": "xx"}` → `None`; pending alert → `None` |
| list still never loads `raw` | `tests/test_country_field.py::test_list_alerts_with_geo_join_still_projects_raw_fields_only` | the `before_cursor_execute` listener sees `alerts.raw ->>` and no bare `alerts.raw`; one SELECT for items (plus the count) |
| detail country | `tests/test_country_field.py::test_get_alert_detail_country_from_its_own_tool_calls` | detail of the same alert → `country == "DE"`; a detail without geo → `None`; no additional statement versus M3 (listener count unchanged: alert, latest verdict, tool_calls) |
| route + baseline | `tests/test_country_field.py::test_list_and_detail_routes_serialize_country_and_baseline_matches` | `GET /api/v1/alerts` item has `"country": "DE"`; `GET /api/v1/alerts/{id}` too; `tests/test_openapi_baseline.py::test_committed_baseline_matches_app` green after regeneration |
| `countryFlag` | `web/src/lib/format.test.ts` — `it("countryFlag maps a two-letter code to regional indicators and everything else to empty")` | `"DE"` → `"🇩🇪"` (`"\u{1F1E9}\u{1F1EA}"`), `"NL"` → `"🇳🇱"`; `"de"`, `"DEU"`, `""`, `null`, `undefined` → `""`; fails when lowercase is mapped |
| `CountryFlag` renders | `web/src/components/ui/CountryFlag/CountryFlag.test.tsx` — `it("renders an img-role element with the aria-label and title for a valid code")` | `getByRole("img", { name: "Country DE" })` has `title="DE"` and text `"🇩🇪"`; the bare text `DE` is not a visible text node (`queryByText("DE")` is null) |
| `CountryFlag` empty | `CountryFlag.test.tsx` — `it("renders nothing for null, undefined, or an invalid code")` | `container.firstChild === null` for `null`, `undefined`, `"xx"`, `"<b>"` — the last also proves no element is created from the input |
| `AlertRow` flag in the IP cell | `AlertRow.test.tsx` — `it("renders the country flag inside the IP cell before the link")` | `country: "DE"` → `within(ipCell).getByRole("img", { name: "Country DE" })`; the link's name is still the IP; still six cells (existing pin) |
| `AlertRow` no flag | `AlertRow.test.tsx` — `it("renders no flag when country is null or absent")` | `country: null` and the property omitted → `queryByRole("img")` null |
| `AlertHeader` flag | `AlertHeader.test.tsx` — `it("renders the country flag in the heading and none when null")` | `"BR"` → heading contains `getByRole("img", { name: "Country BR" })` and the IP text; `null` → no img |
| `Timeline` empty state | `Timeline.test.tsx` — `it("renders the empty state when there are no tool calls")` (replaces the M3 placeholder test) | exactly one `listitem` with text `No tool calls were made.`; `Tool trace arrives at M4` absent |
| `Timeline` items faithful | `Timeline.test.tsx` — `it("renders tool name, arguments, result and latency for each call in order")` | two calls → two items in order; item 0 text contains `0. lookup_ip_reputation`, `12 ms`, `"abuse_score": 87` (from the result JSON) and `"ip": "203.0.113.7"` (from the arguments JSON); item 1 contains `1. get_session_commands` and `340 ms`; fails when arguments or result are omitted or the order is sorted by name |
| `Timeline` escapes | `Timeline.test.tsx` — `it("renders a <script> fragment inside a command as text, never an element")` | `result: {"commands": ["<script>alert(1)</script>"]}` and `arguments: {"session_id": "<img src=x onerror=alert(1)>"}` → both strings visible as text, `container.querySelector("script, img") === null` |
| `Timeline` unavailable marker | `Timeline.test.tsx` — `it("marks an unavailable result visibly with its reason")` | `result: {"unavailable": true, "reason": "no_api_key"}` → text `unavailable (no_api_key)`; a normal result has no `unavailable (` text; `{"unavailable": true}` without a reason → `unavailable (unknown)` |
| `Timeline` latency null | `Timeline.test.tsx` — `it("renders an em dash for a null latency")` | `latency_ms: null` → `—` in the item |
| structure pins | `Timeline.test.tsx` — `it("keeps the ordered list with the Tool trace name and one li per call")` | `getByRole("list", { name: "Tool trace" })` is an `OL`; `getAllByRole("listitem").length === toolCalls.length` |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the two new test files plus the four
re-pinned web test files; the **implementer** does Steps 3–7 and never edits a pinned file;
Step 8 is the controller's.

- [ ] **Step 1 (RED — test-author): write `tests/test_country_field.py` and the web tests** per the
  table (component tests start with `// @vitest-environment jsdom`; fixtures are complete
  `ToolCallOut` / `AlertSummary` / `AlertDetail` literals; `pnpm -C web format` and `pnpm -C web
  lint` clean; note `pnpm -C web type-check` will fail until codegen adds `country` — expected at
  RED, recorded).
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q tests/test_country_field.py`
  → Expected: `ImportError: cannot import name 'normalize_country'`; `pnpm -C web test` → the
  `CountryFlag` file fails with `Failed to resolve import "@/components/ui/CountryFlag"`,
  `format.test.ts` with `countryFlag is not a function`, the `AlertRow`/`AlertHeader`/`Timeline`
  files fail on the new assertions only (`Unable to find role="img"`, `No tool calls were made.`
  not found); every other web file stays green. Pin, commit
  `test(core,web): country field + flag, trace timeline RED (m4 task-07)`.
- [ ] **Step 3 (GREEN — implementer): `normalize_country`, `AlertBase.country`,
  `geo_country_subquery`, the two service changes; `uv run python scripts/export_openapi.py`;
  `pnpm -C web codegen`.** `uv run pytest -q tests/test_country_field.py tests/test_read_schemas.py
  tests/test_alerts_read_service.py tests/test_read_routes.py tests/test_openapi_baseline.py`
  green; `git diff --stat api/openapi.json web/src/types/generated` shows both regenerated.
- [ ] **Step 4 (GREEN — implementer): `countryFlag`, `CountryFlag`, `AlertRow`, `AlertHeader`.**
  `pnpm -C web type-check` clean; the four web files green.
- [ ] **Step 5 (GREEN — implementer): `Timeline.tsx` per the behavior spec.** `pnpm -C web test` all
  green; `pnpm -C web lint && pnpm -C web format:check` clean.
- [ ] **Step 6 (implementer): render against the real stack** — `docker compose -f
  infra/docker-compose.yml up -d --build`, migrate, `uv run python scripts/seed_dev.py
  --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief` (after a `down -v`
  if the M3 rows are still there), then paste:
  `curl -s localhost:3000/alerts | grep -o 'aria-label="Country [A-Z][A-Z]"' | sort -u` → five
  lines (`BR DE NL SG US`); `ID=$(curl -s 'localhost:8000/api/v1/alerts?page_size=50' | python3 -c
  'import json,sys; print(next(i["id"] for i in json.load(sys.stdin)["items"] if i["src_ip"]=="192.0.2.55"))')`;
  `curl -s localhost:3000/alerts/$ID | grep -o 'get_session_commands\|Country DE\|cat /etc/passwd' | sort -u`
  → three lines.
- [ ] **Step 7 (implementer): all gates (Python five + the four web gates + both drift checks) →
  commit:** `feat(core,api,web): country on alert DTOs from the geo trace, country flag, tool-call trace timeline (m4 task-07)`
  with the two trailers; path-scoped `git add core/schemas/alerts_read.py
  core/services/alerts_read.py api/openapi.json web/src`.
- [ ] **Step 8 (controller): M4 acceptance walk (`/milestone-gate m4`)** — from
  `docker compose -f infra/docker-compose.yml down -v`, paste every output into the ledger:
  1. `docker compose -f infra/docker-compose.yml up -d --build && docker compose -f infra/docker-compose.yml ps` → three services `healthy`.
  2. `docker compose -f infra/docker-compose.yml run --rm api uv run alembic upgrade head` → `Running upgrade -> 0001`.
  3. `uv run python scripts/seed_dev.py --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief` → `created=25 skipped=0 failed=0`; again → `created=0 skipped=25 failed=0`.
  4. `ID4=$(curl -s 'localhost:8000/api/v1/alerts?page_size=50' | python3 -c 'import json,sys; print(next(i["id"] for i in json.load(sys.stdin)["items"] if i["src_ip"]=="192.0.2.55"))'); curl -s localhost:8000/api/v1/alerts/$ID4 | python3 -c 'import json,sys; d=json.load(sys.stdin); print([t["tool_name"] for t in d["tool_calls"]], d["country"])'` → `['get_session_commands', 'get_ip_geo_asn'] DE` (PRD §12 M4 clause 1: a successful-login session triggers `get_session_commands`).
  5. `ID1=$(… src_ip=="203.0.113.10" …); curl -s localhost:8000/api/v1/alerts/$ID1 | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["tool_calls"]))'` → `1` (clause 2: a bare port scan completes with ≤ 1 tool call).
  6. `curl -s localhost:3000/alerts/$ID4 | grep -o 'Tool trace\|get_session_commands\|cat /etc/passwd\|Country DE' | sort -u | wc -l` → `4` (the trace renders, with the command text and the flag).
  7. `curl -s localhost:3000/alerts | grep -o 'aria-label="Country [A-Z][A-Z]"' | sort -u | wc -l` → `5`.
  8. `set -a; . ./.env; set +a; uv run pytest -q -m live tests/test_tool_loop_live.py` → both tests pass (both clauses against the real LLM with replayed external tools) — or both skipped, recorded as "no key at the gate" with the owner's decision.
  9. `uv run python -m evals.run --golden evals/golden/v1.jsonl --prompt triage-v1 --prompt triage-v4` (with the key) → two comparable rows into the ledger (never the README) — the same comparison as task-06's default gate; the deployed default follows the task-06 ledger ruling (`triage-v1` or `triage-v4`), and nothing in this task depends on which.
  10. `docker compose -f infra/docker-compose.yml config > /dev/null && echo ok` → `ok`; `git ls-files '*.mmdb' | wc -l` → `0`.
  11. Browser screenshots of `/alerts` (flags visible) and `/alerts/<ID4>` (timeline with arguments/result blocks) at the user checkpoint.
  Then: whole-branch review on the strongest model → fix wave → README status line
  "M4 complete (tool calling); M5 (queue split + routing) next" → PR `feat/m4-tool-calling` →
  `main` → tag `m4` on the merge commit → user checkpoint → write the M5 briefs with
  `superpowers:writing-plans`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_country_field.py tests/test_read_schemas.py tests/test_alerts_read_service.py tests/test_read_routes.py tests/test_openapi_baseline.py   # every test in the table passes
uv run python scripts/export_openapi.py --out /tmp/openapi.json && cmp /tmp/openapi.json api/openapi.json && echo no-drift                      # no-drift
pnpm -C web codegen && git diff --exit-code -- web/src/types/generated && echo no-drift                                                            # no-drift
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test                                                        # all green
grep -rn "dangerouslySetInnerHTML\|use client" web/src ; echo "exit=$?"                                                                            # exit=1
grep -o 'aria-label="Tool trace"' web/src/components/alerts/Timeline/Timeline.tsx | sort -u | wc -l                                               # 1
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q         # all clean
```

## Acceptance

- `/alerts/[id]` renders every persisted tool call in `seq` order with tool name, arguments,
  result and latency as escaped text inside the `Tool trace` `<ol>`; unavailable results are
  marked visibly; an alert with no calls says so.
- `AlertSummary`/`AlertDetail` carry `country` from the latest verdict's first geo result
  (normalized or `null`), the OpenAPI baseline and the web codegen are regenerated in the same
  commit, and the queue and detail IP cells show the flag as an `img`-role element with an
  accessible name — none when the country is unknown or the geo tool was unavailable.
- The PRD §12 M4 acceptance clauses are demonstrated in the walk with pasted output and a
  browser screenshot.
