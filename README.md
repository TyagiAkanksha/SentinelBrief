# SentinelBrief

An LLM-powered triage layer for security alerts. It ingests real attacker sessions from a
self-hosted SSH honeypot (Cowrie), lets a model gather context through tool calls, and gives
analysts a ranked, explained queue instead of raw JSON — with a published evaluation harness
measuring how well it does.

**Status:** M4 complete (tool calling); M5 (queue split + routing) next. The build plan
lives in [`docs/plans/`](docs/plans/README.md); the spec is
[`PRD.md`](PRD.md).

## What it does

1. The honeypot's log shipper posts one HMAC-signed alert per attacker session.
2. A worker runs a triage pipeline: a cheap model reads a session summary, may call enrichment
   tools (IP reputation, geo/ASN, alert history, session commands, asset info), and returns a
   structured **verdict** — severity 1–5, category, confidence, reasoning, recommended action.
   Low-confidence or high-severity verdicts are re-run on a stronger model.
3. A public, read-only dashboard shows the queue and, per alert, the full tool-call trace.
4. An evaluation harness scores every prompt/model change against a hand-labeled golden set and
   publishes the numbers — including the ones that got worse — to [`docs/results.md`](docs/results.md).

The human always decides. The system never blocks, quarantines, or responds automatically.

## Architecture

```
┌─────────────┐   HMAC-signed    ┌──────────────────────────────────────┐
│ Honeypot VM │   HTTPS POST     │              App host                │
│  (Cowrie)   ├─────────────────►│  ┌─────────┐  enqueue  ┌──────────┐  │
│ log shipper │                  │  │ FastAPI ├──────────►│  Redis   │  │
└─────────────┘                  │  └────┬────┘           └────┬─────┘  │
                                 │       │ write raw           │ dequeue│
                                 │  ┌────▼────────┐      ┌─────▼──────┐ │
                                 │  │  PostgreSQL │◄─────┤ ARQ worker │ │
                                 │  └────┬────────┘ write│ (tool loop │ │
                                 │       │        verdict│  + LLM)    │ │
                                 └───────┼───────────────┴────────────┘ │
                                         │ read-only queries
                                 ┌───────▼────────┐
                                 │ Next.js (Caddy) │◄── public, read-only
                                 └────────────────┘
```

Invariants: ingest answers `202` in under 100 ms and all LLM work happens in the worker; no
public request path can ever trigger an LLM call; the honeypot host shares nothing with the app
host except one HMAC secret. Deployment topology: [`docs/deployment.md`](docs/deployment.md).

## Dev quickstart

### Prerequisites

- Docker + Docker Compose v2 (developed with Docker 29 / Compose v5)
- [uv](https://docs.astral.sh/uv/) — for running the Python gates outside a container
- Node 24 + [pnpm](https://pnpm.io/) via corepack (`corepack enable`) — for the dashboard gates

### 1. Configure environment

```sh
cp .env.example .env
```

Fill in `.env` — every variable is documented inline and in PRD §4/§8/§10. Generate the ingest
secret (required in every environment; the API refuses to boot with it empty):

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `LLM_API_KEY`, `CHEAP_MODEL`, and the matching entry in `MODEL_PRICES_JSON`. Any
OpenAI-compatible endpoint works: leave `LLM_BASE_URL` at its default for OpenAI, or point it at
NVIDIA NIM's `https://integrate.api.nvidia.com/v1` with a free key. **Never commit `.env`** (it is
gitignored; only `.env.example` is tracked).

`Settings` (`core/config.py`) never loads `.env` itself — only the compose `api` service does, via
`env_file`. Every host-side command below (step 2, `scripts/seed_dev.py --live`, `worker.
triage_one`) reads plain environment variables, so load `.env` into your shell first:

```sh
set -a; . ./.env; set +a   # host-side commands read the environment only; Settings never loads .env
```

### 2. Run the core loop

```sh
uv sync
uv run python -m worker.triage_one fixtures/alerts/alert1.json
```

Prints one JSON document: the validated verdict plus model, prompt version, token, cost and
latency fields.
Exit codes: `0` on success; `1` on a config, LLM, or invalid-input error; `2` when the verdict
still fails validation after its one retry.

### 3. Run the stack

```sh
docker compose -f infra/docker-compose.yml up -d --build                    # postgres, api, web, redis, worker
docker compose -f infra/docker-compose.yml run --rm api uv run alembic upgrade head
curl -s localhost:8000/healthz                                              # {"status":"ok","db":"ok"}
curl -s localhost:3000/healthz                                              # {"status":"ok"}
# Seed a browsable queue: 5 fixtures + 20 golden v1 sessions through the real pipeline with the fake LLM
# (add --live to spend real tokens with LLM_API_KEY / CHEAP_MODEL / MODEL_PRICES_JSON exported
# (see step 1); a re-run creates nothing).
uv run python scripts/seed_dev.py --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief   # created=25 skipped=0 failed=0
# Open http://localhost:3000/alerts in a browser — the queue; click an IP for the detail page
curl -s 'localhost:8000/api/v1/alerts?page_size=5' | python3 -m json.tool | head -30
# keeps the secret out of shell history and out of this file
export INGEST_HMAC_SECRET=$(grep '^INGEST_HMAC_SECRET=' .env | cut -d= -f2-)
uv run python scripts/post_alert.py fixtures/alerts/alert4.json   # 200 {"created": false} — already seeded; dedup by fingerprint (a session with a new session_id gets 202 {"status": "pending"} and is enqueued to the worker)
docker compose -f infra/docker-compose.yml logs worker | tail                                          # the ARQ job line, once the worker picks it up
curl -s localhost:8000/api/v1/alerts/<id>                                                              # "status":"triaged" within a few seconds
docker compose -f infra/docker-compose.yml exec postgres psql -U sentinel -d sentinelbrief \
  -c "select count(*) from alerts; select count(*) from verdicts;"   # 25 and 25
```

Migrations are **never** run at container startup — the `alembic upgrade head` line above is the
only DDL path.

Triage jobs are idempotent: restarting or `docker kill`-ing the worker mid-job just re-runs the
interrupted job without duplicating verdicts (the attempt holds a row lock and skips alerts that
are no longer `pending`).

#### Enrichment tools (optional)

```sh
uv run python scripts/fetch_geoip.py   # needs MAXMIND_LICENSE_KEY exported (step 1); writes infra/geoip/GeoLite2-Country.mmdb and GeoLite2-ASN.mmdb, never committed
```

Then set `GEOIP_DB_PATH=infra/geoip/GeoLite2-Country.mmdb` and
`GEOIP_ASN_DB_PATH=infra/geoip/GeoLite2-ASN.mmdb` in `.env` (the compose `api` service mounts
`infra/geoip` read-only at the same path). `ABUSEIPDB_API_KEY` is optional too. Without keys both
tools answer `{"unavailable": true}`.

### 4. Tear down

```sh
docker compose -f infra/docker-compose.yml down     # add -v to drop the database volume too
```

## Gates (run before every commit that touches the relevant tree)

Python (repo root):

```sh
# One-time: a dedicated Postgres for the test DB and a dedicated Redis for the test suite (both
# suites skip without their URL and CI fails on any skip). Never point TEST_REDIS_URL at the dev
# compose Redis (6379) — the Redis fixtures flushdb it before AND after every test.
docker run -d --name sentinelbrief-test-db -e POSTGRES_USER=sentinel -e POSTGRES_PASSWORD=sentinel -e POSTGRES_DB=sentinelbrief_test -p 127.0.0.1:5434:5432 postgres:16
docker run -d --name sentinelbrief-test-redis -p 127.0.0.1:6380:6379 redis:7-alpine
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run ruff check --no-cache .
uv run ruff format --check .
uv run mypy --no-incremental
uv run lint-imports
uv run pytest -q
```

Frontend:

```sh
pnpm -C web lint
pnpm -C web type-check
pnpm -C web format:check
pnpm -C web test
```

Live-API smoke tests are opt-in: `uv run pytest -m live`.

## Evaluation

```sh
# external tools replay tests/fixtures/tools (override with --tool-fixtures DIR)
uv run python -m evals.run --golden evals/golden/v1.jsonl --prompt triage-v1 --prompt triage-v2
```

Each `--prompt` value runs the full pipeline over the golden set and produces one comparable row
in the printed table; the full per-case results land as JSON under `evals/results/` (gitignored).
Golden set v1 is synthetic and its numbers are never published; v2 is real, hand-labeled honeypot
traffic and is the only source of the numbers in [`docs/results.md`](docs/results.md) *(from M7)*.
`--strong-model` wires the same two-tier routing (PRD §6.4) into the run, defaulting to
`STRONG_MODEL`; the printed table's `escalation_rate` column reports the fraction of cases each
run escalated to the strong model.

## Deployment

Target topology, secrets handling, backups and the verification checklist:
[`docs/deployment.md`](docs/deployment.md). Scripts and synced production config land under
`infra/deploy/` at M6.

## Working on the repo

- Spec: [`PRD.md`](PRD.md). Conventions: [`CONVENTIONS.md`](CONVENTIONS.md) (Python),
  [`docs/FRONTEND-CONVENTIONS.md`](docs/FRONTEND-CONVENTIONS.md) (web).
- Build plan and execution model: [`docs/plans/README.md`](docs/plans/README.md).
- Claude Code users: [`CLAUDE.md`](CLAUDE.md) plus the agents, skills and rules in `.claude/`.

## Implementation notes

_(Appended by tasks as decisions are made; empty until M0.)_

## License

MIT — see [`LICENSE`](LICENSE).
