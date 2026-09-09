---
id: task-07
milestone: m3-read-path-dashboard
depends_on: [task-05, task-06]
status: planned
spec: PRD.md §11 (the `web` container behind Caddy; `NEXT_PUBLIC_API_URL` baked at build; log rotation), §9 (SSR pages fetch over the compose network), §12 M3 (acceptance: seeded DB renders a browsable queue locally); CONVENTIONS.md §11; .claude/rules/infra.md
---

# task-07 — `infra/Dockerfile.web` (standalone), `web` compose service, README quickstart update, M3 acceptance + tag

## Goal

A multi-stage `node:24-slim` image builds the Next 16 app with pnpm from the workspace root and
runs the standalone server as non-root `nextjs` (uid 1001) with a stdlib `node` healthcheck against
`/healthz`; `infra/docker-compose.yml` gains a `web` service on `127.0.0.1:3000` that waits for a
healthy `api` and reaches it over the compose network as `http://api:8000`; the README quickstart
gains the seed and dashboard steps; the PRD §12 M3 clause ("seeded DB renders a browsable queue
locally") is demonstrated through compose with pasted output, and `m3` is tagged.

## Context (read ONLY these)

- `PRD.md` §9 (Hosting), §11 (Frontend bullet, log rotation), §12 M3.
- `docs/plans/m3-read-path-dashboard.md` — Acceptance walk table.
- `CONVENTIONS.md` §11; `.claude/rules/infra.md` (loopback ports, non-root, no secrets, never
  paste `docker compose config` output — redirect it).
- `infra/Dockerfile.api`, `infra/docker-compose.yml`, `.dockerignore` (already excludes
  `node_modules/`, `**/node_modules/`, `.next/`, `**/.next/`, `**/.env*`, `*.md`, `docs/`),
  `README.md`, `tests/test_dockerfile_pins.py` (the `_instructions()` parser to mirror),
  `tests/test_compose_config.py` (rendered through `docker compose config --format json` from a
  `tmp_path` copy).
- Task-03 outputs: `web/next.config.ts` (`output: "standalone"`, `outputFileTracingRoot` = repo
  root), `web/src/app/healthz/route.ts`, `web/public/robots.txt`, root `pnpm-lock.yaml`,
  `pnpm-workspace.yaml`, root `package.json` (`packageManager: pnpm@11.17.0`; its `prepare`
  script is `lefthook install || true`, harmless in the image). Task-06: `scripts/seed_dev.py`.
- Next 16 standalone facts (v16.3.4 docs, verified at briefing time): `.next/standalone` holds a
  minimal `server.js`; `public/` and `.next/static/` are **not** copied automatically; the server
  reads `PORT` and `HOSTNAME`; with `outputFileTracingRoot` at the workspace root the standalone
  tree mirrors the workspace layout, so the server lands at `.next/standalone/web/server.js` —
  the task-03 implementer report carries the actual `ls web/.next/standalone` listing; if it shows
  `server.js` at the top level instead, the `CMD` becomes `["node", "server.js"]` and the two
  static/public `COPY` destinations drop the `web/` prefix — record which layout was observed.

## Files

- Create: `infra/Dockerfile.web`
- Create (test-author): `tests/test_dockerfile_web_pins.py`
- Modify (test-author, re-pinned): `tests/test_compose_config.py` (+ `web` assertions)
- Modify: `infra/docker-compose.yml` (+ `web` service; header comment updated),
  `README.md` (status line, prerequisites, "3. Run the stack", gates — remove every
  "(from M3)" marker), `.dockerignore` (verify only — no change expected)

## Interfaces

- **Consumes:** the task-03 `web/` app (`pnpm -C web build`, `/healthz`), `scripts/seed_dev.py`
  (task-06), the `api` service's `HEALTHCHECK` (M2), `.env.example`'s `NEXT_PUBLIC_API_URL` /
  `API_URL` defaults.
- **Produces (M6's production compose mirrors these — produce exactly):**

  ```dockerfile
  # infra/Dockerfile.web — build context is the REPO ROOT (compose: context: .., dockerfile: infra/Dockerfile.web).
  # Runtime config is env-only; NEXT_PUBLIC_API_URL is inlined at build (PRD §11) — a new public API origin means a rebuild.
  FROM node:24-slim AS builder
  WORKDIR /repo
  RUN corepack enable
  COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
  COPY web/package.json ./web/package.json
  RUN pnpm install --frozen-lockfile
  COPY web ./web
  ARG NEXT_PUBLIC_API_URL=http://localhost:8000
  ARG API_URL=http://api:8000
  ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL API_URL=$API_URL NEXT_TELEMETRY_DISABLED=1
  RUN pnpm -C web build

  FROM node:24-slim
  RUN groupadd --gid 1001 nodejs && useradd --uid 1001 --gid nodejs --create-home nextjs
  WORKDIR /app
  ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
  COPY --from=builder --chown=nextjs:nodejs /repo/web/.next/standalone ./
  COPY --from=builder --chown=nextjs:nodejs /repo/web/.next/static ./web/.next/static
  COPY --from=builder --chown=nextjs:nodejs /repo/web/public ./web/public
  USER nextjs
  EXPOSE 3000
  HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
  CMD ["node", "web/server.js"]
  ```

  ```yaml
  # infra/docker-compose.yml — new service (postgres and api unchanged; header comment: "Three services: postgres, api, web; worker and redis arrive at M5")
  web:
    build:
      context: ..
      dockerfile: infra/Dockerfile.web
      args:
        NEXT_PUBLIC_API_URL: http://localhost:8000     # the browser-visible API origin for local dev (SSE hook, M8)
        API_URL: http://api:8000
    image: sentinelbrief-web
    environment:
      API_URL: http://api:8000                          # server-side origin over the compose network (FRONTEND-CONVENTIONS §6)
    ports:
      - "127.0.0.1:3000:3000"
    depends_on:
      api:
        condition: service_healthy
    logging: *default-logging
  ```

  `README.md` "3. Run the stack" becomes (exact commands; the compose Postgres is reachable from
  the host on `127.0.0.1:5432`, whereas `.env`'s `DATABASE_URL` uses the compose hostname
  `postgres`, so the seed takes an explicit URL):

  ```sh
  docker compose -f infra/docker-compose.yml up -d --build                    # postgres, api, web
  docker compose -f infra/docker-compose.yml run --rm api uv run alembic upgrade head
  curl -s localhost:8000/healthz                                              # {"status":"ok","db":"ok"}
  # Seed a browsable queue: 5 fixtures + 20 golden v1 sessions through the real pipeline with the fake LLM
  # (add --live to spend real tokens with the key in .env; a re-run creates nothing).
  uv run python scripts/seed_dev.py --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief   # created=25 skipped=0 failed=0
  open http://localhost:3000/alerts                                           # the queue; click an IP for the detail page
  curl -s 'localhost:8000/api/v1/alerts?page_size=5' | python3 -m json.tool | head -30
  ```
  followed by the existing signed-POST lines (`post_alert.py` → 202 / 200) and the psql count
  line updated to `26` and `26`. The status line becomes "M3 complete (read path + dashboard v0);
  M4 (tool calling) next." after the acceptance walk passes; the prerequisites and gates sections
  drop their "(from M3)" markers.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| builder stage | `tests/test_dockerfile_web_pins.py::test_web_builder_is_node_24_slim_with_corepack` | first `FROM` is `node:24-slim AS builder`; a `RUN` contains `corepack enable`; a `RUN` contains `pnpm install --frozen-lockfile`; a `RUN` contains `pnpm -C web build`; the lockfile `COPY` precedes the install |
| runtime non-root | `tests/test_dockerfile_web_pins.py::test_web_runtime_is_non_root_nextjs_uid_1001` | last `FROM` is `node:24-slim`; a `RUN` after it has `useradd`, `1001`, `nextjs`; `USER nextjs` present |
| healthcheck | `tests/test_dockerfile_web_pins.py::test_web_healthcheck_uses_node_fetch_on_healthz` | `HEALTHCHECK` block contains `http://127.0.0.1:3000/healthz` and `"node"`; the word `curl` appears nowhere |
| `CMD` | `tests/test_dockerfile_web_pins.py::test_web_cmd_is_node_standalone_server` | JSON-form `CMD` parses to `["node", "web/server.js"]` (or `["node", "server.js"]` if the task-03 listing showed the flat layout — the test-author reads that report and pins the observed one) |
| standalone copies | `tests/test_dockerfile_web_pins.py::test_web_copies_standalone_static_and_public` | three `COPY --from=builder` whose sources end `/web/.next/standalone`, `/web/.next/static`, `/web/public`, each with `--chown=nextjs:nodejs` |
| no secrets baked | `tests/test_dockerfile_web_pins.py::test_web_no_env_file_copied` | no `COPY`/`ADD` references `.env`; no `ENV` sets `LLM_API_KEY`, `INGEST_HMAC_SECRET`, `ADMIN_TOKEN`, `DATABASE_URL` |
| build args | `tests/test_dockerfile_web_pins.py::test_web_build_args_for_public_and_server_api_url` | `ARG NEXT_PUBLIC_API_URL` and `ARG API_URL` appear before `pnpm -C web build` |
| `web` service shape | `tests/test_compose_config.py::test_compose_web_service_shape` (new) | image `sentinelbrief-web`; `environment.API_URL == "http://api:8000"`; `depends_on.api.condition == "service_healthy"`; build context resolves to the repo-root stand-in and dockerfile ends `infra/Dockerfile.web`; `build.args.NEXT_PUBLIC_API_URL` present; json-file logging with `max-size 10m` |
| loopback + 3000 | `tests/test_compose_config.py::test_compose_publishes_loopback_only` (re-pinned) | adds `published_targets.get("web") == {"3000"}` |
| three services | `tests/test_compose_config.py::test_compose_config_validates` (re-pinned) | adds `"web" in services` |
| no compute in compose | `tests/test_seed_dev.py::test_seed_script_is_not_referenced_by_compose` (task-06, unchanged) | — |
| README quickstart | acceptance walk step 5 (the README commands are the ones executed, verbatim) | — |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins both test files; the **implementer** does
Steps 3–6; the **controller** runs Steps 7–8.

- [ ] **Step 1 (RED — test-author): write `tests/test_dockerfile_web_pins.py`** (copy the
  `_instructions()` parser shape from `tests/test_dockerfile_pins.py`, pointed at
  `infra/Dockerfile.web`) **and extend `tests/test_compose_config.py`** per the table. Read
  `.superpowers/sdd/m3-read-path-dashboard/task-03-implementer.md` for the observed standalone
  layout before pinning the `CMD`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_dockerfile_web_pins.py tests/test_compose_config.py` → Expected: 7 failures on
  `infra/Dockerfile.web does not exist yet`, and the three compose tests failing on the missing
  `web` service (`KeyError: 'web'` / assertion). Pin both files, commit
  `test(infra): web image and compose web service pins RED (m3 task-07)`.
- [ ] **Step 3 (GREEN — implementer): write `infra/Dockerfile.web` and the `web` compose
  service** per Interfaces; update the compose header comment. `docker compose -f
  infra/docker-compose.yml config > /dev/null && echo ok` → `ok` (never print the rendered
  config — it interpolates `.env`).
- [ ] **Step 4 (GREEN — implementer): build and run:** `docker compose -f
  infra/docker-compose.yml up -d --build` → `docker compose -f infra/docker-compose.yml ps`
  shows `postgres`, `api`, `web` all `healthy`; `docker compose -f infra/docker-compose.yml exec
  web id -u` → `1001`; `docker image ls sentinelbrief-web --format '{{.Size}}'` pasted into the
  report.
- [ ] **Step 5 (implementer): README** per Interfaces (status line, prerequisites, "3. Run the
  stack", gates, tear-down unchanged), using exactly the commands you ran.
- [ ] **Step 6 (implementer): tests pass; full gates (Python five + the four web gates) →
  commit:** `chore(infra): web image (standalone) and compose web service; README seed + dashboard quickstart (m3 task-07)`
  with the two trailers; path-scoped `git add infra/Dockerfile.web infra/docker-compose.yml
  README.md tests/test_dockerfile_web_pins.py tests/test_compose_config.py`.
- [ ] **Step 7 (controller): acceptance walk (`/milestone-gate m3`)** — from a clean state
  (`docker compose -f infra/docker-compose.yml down -v`), paste every output into the ledger:
  1. `docker compose -f infra/docker-compose.yml up -d --build && docker compose -f infra/docker-compose.yml ps` → three services, all `healthy`.
  2. `docker compose -f infra/docker-compose.yml run --rm api uv run alembic upgrade head` → `Running upgrade -> 0001`.
  3. `uv run python scripts/seed_dev.py --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief` → `created=25 skipped=0 failed=0`; run again → `created=0 skipped=25 failed=0`.
  4. `curl -s 'localhost:8000/api/v1/alerts?page_size=5' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["items"]), d["total"], [i["verdict"]["severity"] for i in d["items"]])'` → `5 25 [5, 5, 5, 5, 5]`.
  5. `curl -s localhost:3000/alerts | grep -o 'S[1-5]' | sort | uniq -c` → counts for `S1`…`S5` with `S4` ≥ 4 and `S5` ≥ 5; `curl -s 'localhost:3000/alerts?severity_gte=5' | grep -c 'S4'` → `0`.
  6. `ID=$(curl -s 'localhost:8000/api/v1/alerts?page_size=1' | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["id"])'); curl -s localhost:3000/alerts/$ID | grep -c 'Raw session JSON\|Recommended action\|Tool trace'` → `3`.
  7. `curl -s -o /dev/null -w "%{http_code}\n" localhost:3000/alerts/$(uuidgen)` → `404`; `curl -s localhost:3000/healthz` → `{"status":"ok"}`.
  8. `curl -s localhost:8000/api/v1/stats | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["total_alerts"], d["by_severity"], d["latency_p95_ms"])'` → `25 {"1": …, "5": …} <int>`.
  9. `docker compose -f infra/docker-compose.yml config > /dev/null && echo ok` → `ok`.
  10. Browser screenshot of `/alerts` and one `/alerts/[id]` at the user checkpoint (the live pass the spine's acceptance table names).
- [ ] **Step 8 (controller): whole-branch review on the strongest model → fix wave → PR
  `feat/m3-read-path-dashboard` → `main` → tag `m3` on the merge commit → add `web` as a required
  CI check beside `python` → user checkpoint → write the M4 briefs with
  `superpowers:writing-plans`.**

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_dockerfile_web_pins.py tests/test_compose_config.py       # 14 passed
docker compose -f infra/docker-compose.yml config > /dev/null && echo ok               # ok
docker compose -f infra/docker-compose.yml up -d --build && docker compose -f infra/docker-compose.yml ps   # postgres, api, web: healthy
docker compose -f infra/docker-compose.yml exec web id -u                              # 1001
curl -s localhost:3000/healthz                                                         # {"status":"ok"}
grep -c "seed_dev" infra/docker-compose.yml ; echo "exit=$?"                          # 0 / exit=1 (the seed is never a compose command)
docker compose -f infra/docker-compose.yml down                                        # clean stop; add -v to drop data
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test   # all clean
```

## Acceptance

- PRD §12 M3: `docker compose up` (postgres + api + web) → seed → `/alerts` renders the 25-row
  queue with severity badges, `/alerts/[id]` renders reasoning, action, routing, raw JSON and the
  timeline placeholder, an unknown id is a 404 page; the API list/stats answer the same data.
- The web image runs as `nextjs` (uid 1001), bakes no `.env`, healthchecks `/healthz` with
  `node` only; the compose `web` service publishes on loopback, waits for a healthy `api`, uses
  `http://api:8000` server-side and json-file log rotation; `m3` is tagged.
