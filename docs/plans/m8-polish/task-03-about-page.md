---
id: task-03
milestone: m8-polish
depends_on: [task-02]
status: planned
spec: PRD.md §9 page 4 ("/about — 3-paragraph project explanation, architecture diagram, link to results table and repo. Written for a recruiter with 60 seconds."), §3 (the architecture diagram this page reproduces), §1.2–§1.3 (what the system does; the honest framing the copy must not overclaim past), §9 design and accessibility; `docs/FRONTEND-CONVENTIONS.md` §2–§4, §7, §9; `.claude/rules/web.md`
---

# task-03 — `/about`: the 60-second explanation, the architecture diagram, and the outbound links

## Goal

A visitor who has never heard of this project reads one page and understands what it is, how it
works, and where the evidence lives — in about a minute. Three paragraphs of prose (the copy is in
this brief, verbatim — writing it is not the implementer's judgment call), the PRD §3 architecture
diagram rendered as an accessible figure, and links to the repository and the published results
table. The page is static: it fetches nothing, so it cannot fail and it cannot compute.

The copy's job is to be accurate, not impressive. It says plainly that the human decides, that no
public request spends a token, and that the numbers include the runs that got worse.

## Context (read ONLY these)

- `PRD.md` §1.2 (what the system does; the alert unit), §1.3 (honest framing), §3 (the diagram),
  §9 (page 4, the Design paragraph, accessibility).
- `docs/plans/m8-polish.md` — Goal and **Global Constraints** (all of them, including the M8a
  section).
- `docs/FRONTEND-CONVENTIONS.md` §2 (tokens only in `globals.css`), §3 (folder-per-component,
  dumb components), §4, §7 (Vitest, jsdom per file), §9 (accessibility).
- `.claude/rules/web.md`.
- Code you build on: `web/src/app/layout.tsx` (the Primary nav), `web/src/app/stats/page.tsx`
  (task-02 — the page shape and the `metadata` export), `web/src/components/ui/` (folder shape),
  `web/src/app/globals.css` (the tokens).

## Files

- Create: `web/src/lib/site.ts`, `web/src/app/about/page.tsx`,
  `web/src/components/about/ArchitectureDiagram/{ArchitectureDiagram.tsx,interface.ts,index.ts}`
- Modify: `web/src/app/layout.tsx` (nav gains About)
- Create (test-author — the pinned files): `web/src/lib/site.test.ts`,
  `web/src/components/about/ArchitectureDiagram/ArchitectureDiagram.test.tsx`,
  `web/src/app/about/page.test.tsx`

## Interfaces

```ts
// web/src/lib/site.ts — the project's outbound links, in ONE place
export const REPO_URL = "https://github.com/TyagiAkanksha/SentinelBrief";
export const RESULTS_URL = `${REPO_URL}/blob/main/docs/results.md`;
export const PRD_URL = `${REPO_URL}/blob/main/PRD.md`;

// web/src/components/about/ArchitectureDiagram/interface.ts
export type ArchitectureDiagramProps = Record<string, never>;   // no props: the diagram is fixed content
// ArchitectureDiagram.tsx renders:
//   <figure>
//     <div className="overflow-x-auto">          {/* wide content scrolls inside itself */}
//       <pre aria-hidden="true" className="font-mono text-xs leading-tight">{DIAGRAM}</pre>
//     </div>
//     <figcaption>An isolated honeypot host posts each finished SSH session to the app host,
//       where the API stores it and queues it; a worker calls the model and its tools, writes the
//       verdict, and the dashboard reads the database.</figcaption>
//   </figure>
// The <pre> is aria-hidden and the <figcaption> carries the same information in words, so the
// figure is legible to a screen reader without reading box-drawing characters aloud
// (FRONTEND-CONVENTIONS §9). DIAGRAM is a module-level template literal, copied VERBATIM from the
// "Diagram" section below — if a character lands crooked in your editor, fix the alignment and say
// so in the report.
```

### Diagram (copy verbatim into `DIAGRAM`)

```
  Honeypot host (isolated VPC)          App host (one box, behind Caddy)
 +---------------------------+        +-----------------------------------+
 |  Cowrie SSH honeypot      |        |   FastAPI  --enqueue-->  Redis     |
 |         |                 | HMAC   |      |                     |      |
 |         v                 | HTTPS  |      | raw session         | job  |
 |  log shipper  ------------|------->|      v                     v      |
 +---------------------------+  POST  |  PostgreSQL <--verdict--  ARQ     |
                                      |      |                    worker  |
   assume it is compromised           |      | read-only        (tools +  |
   -- that is its job                 |      v                   the LLM) |
                                      |  Next.js dashboard  --> public    |
                                      +-----------------------------------+
```

### Page copy (three paragraphs — verbatim)

```
SentinelBrief triages honeypot alerts with a large language model. A Cowrie SSH honeypot on an
isolated host records every session an attacker opens against it, a shipper posts each finished
session here, and a worker asks a model to judge it: how severe, what kind of activity, how
confident, and what a person should do about it. Every verdict carries written reasoning and the
evidence it rests on. The human always decides. Nothing here blocks an address, isolates a host,
or answers an attacker.

One alert is one honeypot session. The ingest endpoint verifies a signature, stores the raw
session and returns in well under a second; all model work happens in a background worker, so no
page view and no public request ever spends a token. Before deciding, the model may call
enrichment tools: the session's commands, the target host's role, the source address's geography
and reputation, and that address's history against this honeypot. Every tool call is recorded, so
the trace shown on an alert's page is the real one. A second, stronger model re-judges the cases
the first rates severe or is unsure about.

This is a portfolio project, built to be measured rather than demoed. Accuracy, cost and latency
are scored against a labelled set of sessions whenever the prompt or the model changes, and the
numbers are published — including the runs that came out worse than the ones before them. The
dashboard runs on a single host behind Caddy; the honeypot lives in its own network with no shared
credentials, on the assumption that it will be fully compromised. That is its job.
```

```tsx
// web/src/app/about/page.tsx
export const metadata = { title: "About — SentinelBrief" };
// A Server Component with NO data fetching at all (no getJson, no fetch, no dynamic export —
// this page is prerendered). Structure:
//   <h1>About SentinelBrief</h1>
//   the three <p> paragraphs above, in order
//   <ArchitectureDiagram />
//   a <nav aria-label="Project links"> (or a <ul>) with three external links, each with a visible
//   label and an absolute href from @/lib/site:
//     "Evaluation results" -> RESULTS_URL   "Source code" -> REPO_URL   "Product spec" -> PRD_URL
//   plus one internal <Link href="/alerts">Live alert queue</Link>
//   Every external link carries rel="noreferrer" and target="_blank".
//   Styling (ruling R25 — the original brief listed structure only, which produced a page whose
//   links were indistinguishable from body text): links use the repo's existing treatment
//   `text-accent underline` (precedent web/src/components/alerts/AlertRow/AlertRow.tsx); the h1
//   matches /stats (`text-lg font-semibold`); paragraphs sit in a `max-w-prose space-y-3`
//   container so the measure stays readable; the primary nav in layout.tsx gets `flex gap-4`.
// web/src/app/layout.tsx: the Primary nav becomes Alerts · Stats · About.
```

## Briefing rulings (decided)

- **R8 — the repository link ships now, pointing at the canonical URL, even though the repo is
  private today.** Making it public is the owner's action at M8b's README task (PRD §14 item 2);
  the page must not carry a placeholder or a second, temporary URL. Cost if wrong: one constant.
- **R9 — the diagram is ASCII in a `<pre>`, not an image or a chart library.** It is the PRD's own
  diagram, it costs no dependency and no asset pipeline, and the `<figcaption>` carries the same
  content for a screen reader. Cost if wrong: a nicer picture later.
- **R25 — the page carries the dashboard's existing link, heading and measure treatment.** The
  brief prescribed structure with no styling, so a literal implementation shipped links with the
  body-text colour and no underline (review I1, measured in headless Chrome). Fixed in the task's
  fix round with the repo's own token classes; a dashboard-wide pass over the older unstyled links
  (layout nav, FilterBar, Pagination) is a deferred Minor for the M8a final review.
- **R26 — two copy claims are true of the code and of the production compose, but the results
  table is empty until M7.** The linked results page states "No published runs yet — the first
  appears at M7" in its own text, so a reader who follows the link sees the truth; the sentence
  stands, and the owner is shown this at the M8a checkpoint in case they prefer a softer wording
  until the first v2 row lands. `STRONG_MODEL: gpt-5.4` is set in
  `infra/deploy/prod/docker-compose.yml` **on `feat/m6-real-data-deploy`** (that file does not exist
  on this branch); the escalation sentence is true where the page is served once M8a has rebased
  onto `main` after `m6` — re-check at the M8a deploy gate.
- **R10 — the copy is fixed by this brief.** A reviewer checks the rendered text against these three
  paragraphs. If the implementer believes a sentence is inaccurate, it stops and says so rather than
  rewording silently: the claims are load-bearing (PRD §1.3 honesty).

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `site.ts` links | `web/src/lib/site.test.ts` — `it("exposes absolute https project links under the repo URL")` | all three start `https://`; `RESULTS_URL` and `PRD_URL` start with `REPO_URL`; `RESULTS_URL` ends `/docs/results.md`; no trailing whitespace |
| diagram figure | `.../ArchitectureDiagram.test.tsx` — `it("renders a figure whose caption describes the flow")` | `container.querySelector("figure")` present; the caption text mentions the honeypot, the worker and the dashboard |
| diagram is decorative to AT | `it("hides the ASCII art from assistive technology")` | the `<pre>` carries `aria-hidden="true"`; its text contains `Cowrie`, `PostgreSQL`, `ARQ` |
| diagram cannot overflow the page | `it("wraps the diagram in a horizontally scrollable container")` | the `<pre>`'s parent element's `className` contains `overflow-x-auto` |
| the three paragraphs | `web/src/app/about/page.test.tsx` — `it("renders the three approved paragraphs in order by opening clause")` (the shipped test keeps the RED name; this row's wording is corrected so the next brief copying it does not overclaim — review conflict 2) | `container.querySelectorAll("p")` — the three paragraphs are found in document order and each starts with its approved opening clause (`SentinelBrief triages honeypot alerts`, `One alert is one honeypot session.`, `This is a portfolio project`) |
| the load-bearing claims | `it("states the claims the project is held to")` | the rendered text contains `The human always decides.`, `no page view and no public request ever spends a token`, and `including the runs that came out worse` |
| links render | `it("links to the results table, the repo, the spec and the live queue")` | four links by accessible name; the three external ones carry the `@/lib/site` hrefs, `target="_blank"` and `rel` containing `noreferrer`; the internal one is `/alerts` |
| one heading | `it("has a single level-one heading")` | `screen.getAllByRole("heading", { level: 1 })` has length 1 |
| the page fetches nothing | `it("renders without any network access")` | `vi.stubGlobal("fetch", vi.fn(() => { throw new Error("no fetch on /about"); }))` → the page still renders; the stub was never called |
| nav | `it("links to About from the primary nav")` | rendering `RootLayout`'s nav (or asserting on `layout.tsx`'s content) shows Alerts, Stats and About |

## Steps (TDD)

- [ ] **Step 1 (RED — test-author): write the three test files** per the table. `// @vitest-environment
  jsdom` on line 1 of the two component/page tests; `web/src/lib/site.test.ts` stays in the default
  node environment. Import `describe/it/expect/vi` explicitly. Prettier-format before committing.
- [ ] **Step 2 (RED — test-author): prove they fail.** `pnpm -C web test` → Expected: unresolved
  imports for `@/lib/site`, `@/components/about/ArchitectureDiagram` and `@/app/about/page`. Paste
  the output, sha256 the three files, commit
  `test(web): about page, architecture diagram and site links RED (m8a task-03)` with the two
  trailers.
- [ ] **Step 3 (GREEN — implementer): `web/src/lib/site.ts`**, then `pnpm -C web test src/lib/site.test.ts`
  → pass.
- [ ] **Step 4 (GREEN — implementer): `ArchitectureDiagram`** (folder-per-component: component,
  `interface.ts`, `index.ts` barrel), diagram copied verbatim from this brief.
  `pnpm -C web type-check` then `pnpm -C web test` → the diagram tests pass.
- [ ] **Step 5 (GREEN — implementer): `web/src/app/about/page.tsx`** with the three paragraphs
  verbatim, the diagram, the four links; then the nav link in `web/src/app/layout.tsx`.
  `pnpm -C web test` → all pass.
- [ ] **Step 6 (implementer): read it as a visitor.** `pnpm -C web dev`, open `/about`, and confirm
  the page renders in under a screenful of scrolling on a 1280×800 viewport with the diagram
  scrolling inside its own container rather than widening the page. Report what you saw in one
  sentence; if the dev server is unavailable, say so explicitly rather than implying you looked.
- [ ] **Step 7 (implementer): gates, then one commit.**
  ```bash
  export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
  uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
  pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
  ```
  The Python gates must stay green even though this task touches no Python — run them anyway and
  paste the result. Path-scoped `git add`, message
  `feat(web): /about — project explanation, architecture diagram and project links (m8a task-03)`
  plus the two trailers. Re-verify and report the three pinned sha256 values.

## Verify

```bash
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
grep -rn "#[0-9a-fA-F]\{6\}" web/src --include='*.tsx' --include='*.ts'; echo "exit=$?"   # exit=1
grep -rn "fetch\|getJson" web/src/app/about/page.tsx; echo "exit=$?"                      # exit=1 (the page fetches nothing)
grep -c "_URL = " web/src/lib/site.ts                                                     # 3 constants (only REPO_URL carries a literal "http"; the other two interpolate it)
API_URL=http://127.0.0.1:8000 pnpm -C web build                                           # /about prerenders as static
```

## Acceptance

- `/about` renders the three approved paragraphs in order, the PRD §3 diagram as an accessible
  figure whose caption carries the same meaning, and working links to the results table, the
  repository, the spec and the live queue.
- The page performs no data fetching, prerenders as a static route, and keeps the whole dashboard's
  gates green; the primary nav offers Alerts, Stats and About.
