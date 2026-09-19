# SentinelBrief — Frontend Conventions (`web/`)

**Scope:** the one Next.js app at `web/`. Python rules live in
[`../CONVENTIONS.md`](../CONVENTIONS.md). The PRD ([`../PRD.md`](../PRD.md)) wins on any conflict.

Adapted from the author's AdvisorDesk frontend conventions with two deliberate differences:
SentinelBrief uses **Tailwind** (PRD §4) rather than Material UI, and it has **one** public,
read-only app with no login anywhere (PRD §9).

---

## 1. Workspace & scripts

- pnpm workspace at the repo root (`package.json`, `pnpm-workspace.yaml` with `packages:
  ["web"]`); the app lives at `web/`. pnpm's version is pinned by the root `packageManager` field
  and activated with `corepack enable`. Node 24.
- Pinned dev ports: **web 3000, API 8000** — matching `CORS_ORIGINS` and `NEXT_PUBLIC_API_URL` in
  `.env.example`.
- The app exposes these scripts, and **every script is a gate**: a red result is the failing test
  telling you what to fix. `dev` · `build` · `start` · `lint` · `lint:fix` · `format` ·
  `format:check` · `type-check` · `test` · `test:watch` · `codegen`.
- TypeScript `strict: true` plus `noUncheckedIndexedAccess: true`. `any` is banned; document any
  exception inline with the reason.
- **Run `type-check` (tsc) after every implementation or significant change** — not only before
  commits. Type drift caught immediately is cheap; caught at the gate it hides which change
  caused it.

## 2. UI stack: Tailwind (no component library)

- Tailwind CSS v4 (CSS-first) with the design tokens declared **once** in `src/app/globals.css`
  under `@theme`: colors (`--color-sev-1`…`--color-sev-5`, `--color-surface`, `--color-muted`,
  the rest of the severity/surface palette) and the font stacks (`--font-sans`, `--font-mono`).
  Spacing and radii use Tailwind v4's built-in defaults rather than a separate token set; there
  is no `tailwind.config.ts`. `globals.css` is the design-token system; no hex value appears in a
  component.
- Look: **dark, dense, legible** (PRD §9). One font stack, a tight vertical rhythm, tabular
  numbers for timestamps and costs, severity communicated by text *and* color (§9 accessibility).
- A style used twice becomes a primitive in `src/components/ui/` (`Badge`, `Card`, `DataTable`,
  `Timeline`, `Stat`, `CodeBlock`), never a copy-pasted class string. Ad-hoc utility classes are
  fine for one-off layout spacing.
- No CSS modules, no styled-components, no global CSS beyond `globals.css`.

## 3. Component architecture

- **Folder-per-component:** `<Name>/{Component.tsx, interface.ts, index.ts[, Component.test.tsx]}`;
  the barrel re-exports the component and its props type.
- **Components are dumb.** They render props and raise events. Data fetching, SSE handling and
  polling fallbacks live in hooks (`src/hooks/`) or server components, never inside a
  presentational component. Formatting *logic* is centralized in `src/lib/format.ts`'s pure
  helpers (`formatUsd`, `formatCount`, `formatUtc`, …); a presentational component (a table cell,
  a stat card) calls one of these directly on the value it renders — the component never
  reimplements rounding, locale or unit logic itself, it only calls the shared helper.
- Pages under `src/app/` compose components; they do not define UI inline beyond layout.
- Small files, one purpose. A component over ~150 lines is a split-smell.

## 4. Primitives layer

- `src/components/ui/` wraps Tailwind once. Feature components import primitives, never raw
  `className` soup for anything that carries meaning (severity, status, category).
- `Badge` takes a `severity: 1 | 2 | 3 | 4 | 5` (or a category) and owns the color mapping and the
  visually-hidden text; nothing else decides what "severity 4" looks like.
- `Timeline` renders an ordered list (`<ol>`) of tool-call steps: tool name, arguments, result,
  latency. It is the interview artifact (PRD §9) — keep it faithful to the trace, not decorative.

## 5. Types — one codegen boundary

- API types come from `openapi-typescript` over the committed baseline:
  `openapi-typescript ../api/openapi.json -o src/types/generated/schema.d.ts` (the `codegen`
  script). Never hand-write a type that mirrors an API DTO.
- The generated file is committed; CI re-runs codegen and fails on drift. A route/DTO change
  regenerates `api/openapi.json` **and** this file in the same commit (CONVENTIONS.md §8).
- `src/lib/api/` holds the thin typed fetchers keyed by `operation_id`.

## 6. Data fetching

- **List, detail and stats pages are React Server Components** that fetch server-side through
  `src/lib/api/server.ts` using the server-only `API_URL` (the compose-network origin,
  `http://api:8000`), with `cache: 'no-store'`; the API already caches (PRD §8).
- **The SSE hook is client-side** (`useAlertStream`) and talks to the browser-visible
  `NEXT_PUBLIC_API_URL`. It falls back to 30 s polling when the stream drops (PRD §9).
- `NEXT_PUBLIC_*` values are **inlined at `next build`** — one image per environment; changing the
  public API origin means a rebuild, not a restart. `API_URL` is read at runtime *and* needed at
  build time (prerender), so the Dockerfile passes it as both a build arg and a runtime env var.
  Both modules throw when their variable is unset under `NODE_ENV=production`: a missing env var
  in a production build is a deploy-config bug, not a runtime edge case to degrade around.
- **Nothing on any page triggers compute** (PRD §9): no page or hook may call an endpoint that
  runs triage. The only write endpoint (`retriage`) is admin-token gated and has no UI.

## 7. Testing

- Vitest 4 with `environment: 'node'` as the default; component tests opt into jsdom per file
  with a `// @vitest-environment jsdom` pragma on line 1. `globals: false` — `describe/it/expect/vi`
  are imported explicitly.
- React Testing Library + `user-event`; `vitest.setup.ts` registers RTL `cleanup()` in
  `afterEach`.
- Test what the user sees: rendered severity text, timeline order, empty and error states, the
  polling fallback engaging when the stream errors. Mock only the network seam (fetch/EventSource),
  never a component's internals.
- Colocate: `Component.test.tsx` next to `Component.tsx`; hooks get `useX.test.ts`.

## 8. Lint & format

- ESLint 9 flat config with `eslint-config-next` and `eslint-config-prettier`; Prettier is the
  formatter (`format:check` is a gate). Authored tests must be Prettier-clean before hand-off.
- No `console.log` in shipped code; `console.error` only in the documented error boundary.

## 9. Accessibility & UX defaults

- Severity and category are never color-only: the badge text is the label.
- Tables have a `<caption>`; the timeline is an `<ol>`; interactive elements have accessible
  names; focus is visible.
- Timestamps render in UTC with a relative hint; costs and latencies use tabular numbers.
- Empty states and error states are explicit components, not blank space.
