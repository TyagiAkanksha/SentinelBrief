---
id: task-08
milestone: m8-polish
depends_on: [task-05]
status: planned
spec: PRD.md §9 (dashboard: dark, dense, legible, no compute-triggering controls, no login); docs/FRONTEND-CONVENTIONS.md (Tailwind tokens in globals.css, dumb components, folder-per-component, Vitest, tsc after every change); owner request 2026-09-19 ("keep the dark theme, but the layout/tabulation must be as clean as my portfolio / AdvisorDesk")
---

# task-08 — UI polish: adopt the portfolio's token system, a real app shell, a light/dark toggle, and a proper table treatment

## Goal
The dashboard keeps its dark look but gains the clean structure of the owner's portfolio: a full
design-token system (light + dark), a styled app shell with a centered container and a theme
toggle, a shared page header, and a real `DataTable` treatment. **Reference the owner's portfolio
source directly** at `/home/ak/Documents/github_akanksha/tyagiakanksha.github.io/src` (Next + Tailwind
v4 + next-themes): read its `app/globals.css` (the token pattern), `components/top-bar.tsx`,
`components/theme-toggle.tsx`, `components/section.tsx`, `components/badge.tsx` for the aesthetic —
match its calm spacing, muted hierarchy, and hover treatments. No behavior/API change; every
existing Vitest test stays green; add render tests for the new components.

## Constraints
- Keep the token NAMES the components already use (`--color-bg`, `--color-surface`, `--color-border`,
  `--color-text`, `--color-muted`, `--color-accent`, `--color-sev-1..5`) so existing classes keep
  working; ADD `--color-faint`, `--color-accent-soft`, `--color-ring`, `--color-surface-2`, a
  `--radius`, and a display font. Restructure `globals.css` to the portfolio's pattern: raw palette
  vars on `:root` (light) and `[data-theme="dark"]` (dark), mapped in `@theme inline` to the
  `--color-*` names.
- Dark stays the current palette; light is a coherent sibling (GitHub-primer-like). Exact values:

| token | light | dark (keep current) |
|---|---|---|
| bg | #ffffff | #0b0f14 |
| surface | #f6f8fa | #131a22 |
| surface-2 | #eef1f4 | #1b232d |
| line (→border) | #d0d7de | #24303d |
| fg (→text) | #1f2328 | #e6edf3 |
| muted | #59636e | #8b98a5 |
| faint | #818b98 | #6e7681 |
| accent | #0969da | #58a6ff |
| accent-strong | #0550ae | #79b8ff |
| accent-soft | rgba(9,105,218,.08) | rgba(88,166,255,.12) |
| ring | rgba(9,105,218,.40) | rgba(88,166,255,.50) |
| sev-1 | #6e7781 | #6e7681 |
| sev-2 | #1a7f37 | #3fb950 |
| sev-3 | #9a6700 | #d29922 |
| sev-4 | #bc4c00 | #f0883e |
| sev-5 | #cf222e | #f85149 |

- `--radius: 0.5rem`; keep `font-variant-numeric: tabular-nums` on the body; `:focus-visible` uses
  `--color-ring`; respect `prefers-reduced-motion`.
- Add deps to `web/`: `next-themes` (theme toggle, `attribute="data-theme"`, `defaultTheme="dark"`,
  `enableSystem`) and `lucide-react` (sun/moon + a small wordmark mark); Quicksand as the display
  font via `next/font/google` (no dep). Update `pnpm-workspace.yaml` allowBuilds if the install
  needs it. Codegen/openapi unaffected (no wire change).

## Deliverables
1. **`web/src/app/globals.css`** — the token restructure above (light+dark, new tokens, radius,
   display font var), plus base styles: body bg/fg, links (`text-accent hover:text-accent-strong`),
   focus ring, `::selection`.
2. **`web/src/app/layout.tsx`** — wrap in a `ThemeProvider` (next-themes, `suppressHydrationWarning`
   on `<html>`); a styled sticky top bar: left a wordmark ("SentinelBrief" + a small shield/mark),
   center/right the nav (Alerts/Stats/About with an active-state underline/accent via the existing
   `NavLink` `aria-current`), far right a `ThemeToggle`; a centered `<main class="mx-auto max-w-*
   px-* py-*">` container; a slim footer (repo/spec links, muted).
3. **`web/src/components/ui/ThemeToggle/`** (new, dumb+client) — a button toggling light/dark via
   `useTheme`, sun/moon icon, accessible label; render test.
4. **`web/src/components/ui/PageHeader/`** (new) — `title`, optional `subtitle`, optional `actions`
   slot (right-aligned; the `/alerts` live indicator goes here); used on all three pages; render test.
5. **`web/src/components/ui/DataTable/DataTable.tsx`** — restyle: a `rounded-[--radius] border
   border-border` surface frame, `thead` with `text-faint text-xs uppercase tracking-wide` labels +
   bottom rule, `tbody` rows with `border-t border-border` separators and `hover:bg-surface-2`,
   consistent cell padding (`px-3 py-2`), numeric columns right-aligned + tabular-nums (support a
   per-column `align` in the column def). Apply consistently through `AlertRow`, `CostTable`,
   `DistributionTable` (adjust their cell classes; keep their data/logic unchanged).
6. **Primitives**: `Badge` severity as filled pills (keep the sev tokens, tighten padding/rounding);
   `Stat` as a card using `surface`/`radius`/`faint` label; `BudgetBanner` (task-05) styled with the
   sev-4/accent-soft treatment; `Card`/surface class consistency.
7. **Apply** the shell + `PageHeader` across `/alerts`, `/stats`, `/about`; ensure `/about` reads
   clean (it is recruiter-facing) — a centered prose column, the architecture diagram framed, links
   styled.

## Steps (implementer)
- Read the portfolio source (above) first. Install deps. Do the token restructure; verify existing
  pages still render (tsc + existing Vitest green) before restyling. Then shell → PageHeader →
  DataTable → primitives → pages. Add render tests for `ThemeToggle` and `PageHeader`. `tsc`
  (`pnpm -C web type-check`) after each component.
- Keep EVERY existing web test green; do not change any component's props/logic (dumb components,
  render props). If a pinned test asserts exact old classNames, STOP and report (className churn in
  a pinned test needs controller approval) — but most tests assert roles/text/structure, which the
  restyle preserves.

## Verify
```
pnpm -C web lint && pnpm -C web type-check && pnpm -C web test && pnpm -C web format:check && pnpm -C web build
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
uv run python scripts/export_openapi.py   # api/openapi.json unchanged (no wire change)
```
`pnpm -C web build` must succeed (Turbopack) — the controller then does a live browser pass on both themes.

## Acceptance
The dark theme is preserved and a light theme toggles cleanly; the shell has a centered container +
styled top bar + toggle; tables are properly tabulated (headers, separators, hover, aligned
numerics); `/alerts`, `/stats`, `/about` share a page header and read as one clean, portfolio-grade
design; no behavior/API change; all gates green.
