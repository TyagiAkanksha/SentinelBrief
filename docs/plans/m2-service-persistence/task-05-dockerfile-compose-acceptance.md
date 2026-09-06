---
id: task-05
milestone: m2-service-persistence
depends_on: [task-04]
status: planned
spec: PRD.md §11 (image/compose shape), §12 M2 (acceptance clauses); CONVENTIONS.md §11
---

# task-05 — `infra/Dockerfile.api`, `infra/docker-compose.yml`, README quickstart, M2 acceptance + tag

## Goal

A multi-stage uv image runs the API as a non-root user with a stdlib healthcheck and the `uv`
binary available for migrations; `infra/docker-compose.yml` brings up `postgres` and `api` on
loopback ports; the README quickstart is real; the PRD §12 M2 clauses are demonstrated through
compose and `m2` is tagged.

## Context (read ONLY these)

- `PRD.md` §11 (Phase 1 shape — dev compose is the local mirror), §12 M2.
- `CONVENTIONS.md` §11 · `.claude/rules/infra.md`.
- AdvisorDesk `infra/Dockerfile.api` and `infra/docker-compose.yml` (read-only) as templates;
  **do not copy the `DATABASE_URL_SHELL_OVERRIDE` mechanism** — Postgres is a fixed compose
  service here.
- `scripts/post_alert.py` (task-03); `README.md`.

## Files

- Create: `infra/Dockerfile.api`, `infra/docker-compose.yml`, `tests/test_dockerfile_pins.py`,
  `tests/test_compose_config.py`
- Modify: `README.md` (de-stub "3. Run the stack" and "Gates"; remove "(from M2)" markers),
  `.dockerignore` (verify nothing under `infra/` is excluded)

## Interfaces

- **Consumes:** `api.main:app`, `alembic/`, `scripts/post_alert.py`, `.env.example` defaults.
- **Produces (later tasks rely on — produce exactly):**
  - `infra/Dockerfile.api`: stage 1 `FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS
    builder`; `ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1`; `COPY pyproject.toml uv.lock ./`;
    `RUN uv sync --frozen --no-dev --no-install-project`; `COPY api ./api`, `worker ./worker`,
    `core ./core`, `evals ./evals`, `alembic ./alembic`, `alembic.ini ./alembic.ini`;
    `RUN uv sync --frozen --no-dev`. Stage 2 `FROM python:3.12-slim-bookworm`;
    `useradd --create-home --uid 1001 appuser`; copy `.venv`, sources and `/usr/local/bin/uv`
    with `--chown=appuser:appuser`; `ENV PATH="/app/.venv/bin:$PATH"`; `USER appuser`;
    `EXPOSE 8000`; `HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD
    ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]`;
    `CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]`.
    Top comment: build from the repo root context; runtime config is env-only; migrations never
    run at start.
  - `infra/docker-compose.yml`: `postgres` (`image: postgres:16`, `environment:
    POSTGRES_USER: sentinel, POSTGRES_PASSWORD: sentinel, POSTGRES_DB: sentinelbrief` — dev-only
    values matching `.env.example`'s `DATABASE_URL`, `ports: ["127.0.0.1:5432:5432"]`, `volumes:
    [sentinelbrief_pg:/var/lib/postgresql/data]`, `healthcheck: pg_isready -U sentinel -d
    sentinelbrief`); `api` (`build: {context: .., dockerfile: infra/Dockerfile.api}`, `image:
    sentinelbrief-api`, `env_file: ../.env`, `ports: ["127.0.0.1:8000:8000"]`, `depends_on:
    postgres: {condition: service_healthy}`); named volume `sentinelbrief_pg`. The `worker`,
    `redis`, `web` services arrive at M5/M3.
  - Tests: `tests/test_dockerfile_pins.py::test_runs_as_non_root` (`USER appuser` present after
    the last `FROM`), `::test_healthcheck_present`, `::test_no_env_file_copied` (no `COPY`
    line references `.env`); `tests/test_compose_config.py::test_compose_config_validates`
    (`docker compose -f infra/docker-compose.yml config` exit 0; `pytest.skip` when `docker` is
    not on PATH — recorded as a skip).

## Steps (TDD)

- [ ] **Step 1: Write the failing pin tests** (they fail on a missing file).
- [ ] **Step 2: Write `infra/Dockerfile.api` and `infra/docker-compose.yml`** per Interfaces.
- [ ] **Step 3: Build and run:** `docker compose -f infra/docker-compose.yml up -d --build` →
  both containers healthy; `docker compose -f infra/docker-compose.yml run --rm api uv run
  alembic upgrade head` → migration applied.
- [ ] **Step 4: README quickstart de-stubbed with the exact commands used in Step 3 and the
  acceptance walk.**
- [ ] **Step 5: Tests pass; full gates → commit:**
  `chore(infra): api image and dev compose (api + postgres); README quickstart (m2 task-05)`.
- [ ] **Step 6: Acceptance walk** (`/milestone-gate m2`) — paste every output into the ledger:
  1. `curl -s localhost:8000/healthz` → `{"status":"ok","db":"ok"}`.
  2. `export INGEST_HMAC_SECRET=<the .env value>; uv run python scripts/post_alert.py
     fixtures/alerts/alert4.json` → `202 {"id": ..., "status": "triaged", "created": true}`
     (with a real key in `.env`) — or `"failed"` if the key is absent; either proves the path.
  3. `docker compose -f infra/docker-compose.yml exec postgres psql -U sentinel -d sentinelbrief
     -c "select count(*) from alerts; select count(*) from verdicts;"` → `1` and `1`.
  4. Repeat step 2 → `200 {..., "created": false}`; repeat step 3 → counts unchanged.
  5. `curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/api/v1/alerts -H
     'content-type: application/json' -d @fixtures/alerts/alert4.json` → `401`.
  6. `docker compose -f infra/docker-compose.yml config > /dev/null && echo ok` → `ok`.
- [ ] **Step 7: Whole-branch review → fix wave → PR → merge → tag `m2`; then write the M3 briefs
  with `superpowers:writing-plans`.**

## Verify

```bash
docker compose -f infra/docker-compose.yml up -d --build && docker compose -f infra/docker-compose.yml ps   # api healthy, postgres healthy
docker compose -f infra/docker-compose.yml run --rm api uv run alembic upgrade head                        # INFO ... Running upgrade -> 0001
uv run pytest -q tests/test_dockerfile_pins.py tests/test_compose_config.py                                # 4 passed
docker compose -f infra/docker-compose.yml down                                                            # clean stop; add -v to drop data
```

## Acceptance

- PRD §12 M2: compose (api+postgres) → signed POST → one row in `alerts` and one in
  `verdicts`; duplicate POST → 200, no new row; unsigned → 401; image builds; compose config
  validates.
- The container runs as `appuser`; no `.env` is baked into the image; migrations run only via
  the documented command.
