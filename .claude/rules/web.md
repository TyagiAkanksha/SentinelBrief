---
paths: web/**
---

# Rules for `web/` (Next.js dashboard)

- TypeScript `strict` plus `noUncheckedIndexedAccess`; `any` is banned. Run `pnpm -C web
  type-check` after every significant change.
- **Tailwind only**, with tokens defined once in `src/app/globals.css` under `@theme`; no
  `tailwind.config.ts` (Tailwind v4). No hex values in components; no CSS modules; no component
  library.
- Folder-per-component (`<Name>/{Component.tsx, interface.ts, index.ts, Component.test.tsx}`).
  Components are dumb: props in, events out. Fetching, SSE, polling and formatting live in hooks
  or server components.
- One codegen boundary: API types come from `openapi-typescript` over `api/openapi.json` into
  `src/types/generated/`. Never hand-write a type that mirrors an API DTO; regenerate in the
  same commit as any API change.
- List/detail/stats pages are React Server Components fetching via the server-only `API_URL`;
  only the SSE hook runs in the browser via `NEXT_PUBLIC_API_URL` (build-time inlined — one
  image per environment). Both throw when unset under `NODE_ENV=production`.
- **Nothing on any page triggers compute** (PRD §9): no call to retriage or any write endpoint,
  no login UI anywhere.
- Severity and category are never color-only; the badge text is the label. Tables carry a
  `<caption>`; the tool-call timeline is an `<ol>` that shows tool, arguments, result, latency
  faithfully — it is the interview artifact.
- Vitest: `environment: 'node'` default, `// @vitest-environment jsdom` per component test,
  `globals: false`, RTL + `user-event`; mock only the network seam.
- Prettier-clean before hand-off; no `console.log` in shipped code.
