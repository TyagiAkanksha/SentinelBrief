# Environment variable checklist (deployed)

Every variable SentinelBrief reads in production (PRD §11; `core/config.py`'s `Settings` class is
the single source of truth; `.env.example` is the local-dev mirror of the same roster), grouped by
which deployed service needs it. **No secret VALUE appears in this file or any other committed
file** — secrets are named here, with a description of where their real value comes from; the real
values live in **SSM Parameter Store under `/sentinelbrief/`** (written by the owner from their
own terminal, read + decrypted by the app host's EC2 instance role and rendered into the box-local
`/opt/sentinelbrief/.env` / `/opt/sentinelbrief/.env.postgres` by `fetch-secrets.sh` — see
`prod/README.md`), never in a file in this repo. That role's read scope is `/sentinelbrief/*`
because an explicit Deny (`iam/app-host-deny.json`) narrows it there — the attached
`AmazonSSMManagedInstanceCore` managed policy allows `ssm:GetParameter` on `Resource: "*"` on its
own, so the inline Allow is intent and the Deny is the boundary (M6 final review I1). The honeypot
host's role is denied every parameter read outright (`iam/honeypot-host-deny.json`): none of the
values below is reachable from it.

## `api` / `worker`

Both containers share the production compose file's `x-shared-env` block plus `env_file:
[/opt/sentinelbrief/.env]`; `api` alone also sets `CORS_ORIGINS` and `FORWARDED_ALLOW_IPS`. Every
`core.config.Settings` field is listed once below (default values are the `.env.example` ones
unless the production compose file pins something else).

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `LLM_API_KEY` | **Y** | `.env` (SSM) | Your OpenAI API key — platform.openai.com → API keys. |
| `LLM_BASE_URL` | N | compose (pinned) | `https://api.openai.com/v1` |
| `LLM_JSON_MODE` | N | default | `json_object` (`.env.example` default). |
| `LLM_TIMEOUT_S` | N | default | `60` seconds — per-request LLM HTTP timeout (`.env.example` default). |
| `CHEAP_MODEL` | N | compose (pinned) | `gpt-4o-mini` |
| `STRONG_MODEL` | N | compose (pinned) | `gpt-5.4` |
| `ESCALATE_SEVERITY_GTE` | N | default | `4` (`.env.example` default, PRD §6.4). |
| `ESCALATE_CONFIDENCE_LT` | N | default | `0.6` (`.env.example` default, PRD §6.4). |
| `MODEL_PRICES_JSON` | N | compose (pinned) | USD/million-token prices for `gpt-4o-mini` and `gpt-5.4` — see `infra/deploy/prod/docker-compose.yml`'s `x-shared-env`. |
| `TRIAGE_PROMPT_VERSION` | N | compose (pinned) | `triage-v4` |
| `ENVIRONMENT` | N | compose (pinned) | `production` — turns on boot-time required-secret guards. |
| `DATABASE_URL` | **Y** | `.env` (SSM) | Postgres DSN for the compose `postgres` service — embeds `POSTGRES_PASSWORD` below (the same password). |
| `REDIS_URL` | N — pinned in compose (`redis://redis:6379/0`, no password on the compose network); `Settings` keeps it `SecretStr` because a URL MAY embed a password | compose (pinned) | `redis://redis:6379/0` |
| `REDIS_SOCKET_TIMEOUT_S` | N | default | `2` seconds (`.env.example` default). |
| `TRIAGE_JOB_TIMEOUT_S` | N | default | `120` seconds (`.env.example` default). |
| `TRIAGE_ATTEMPT_TIMEOUT_S` | N | default | `100` seconds (`.env.example` default). |
| `WORKER_MAX_JOBS` | N | default | `4` (`.env.example` default). |
| `WORKER_HEALTH_CHECK_INTERVAL_S` | N | default | `15` seconds (`.env.example` default). |
| `INGEST_HMAC_SECRET` | **Y** | `.env` (SSM) | Shared secret with the honeypot host's shipper. Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `INGEST_MAX_BODY_BYTES` | N | default | `2000000` bytes — equals Caddy's `request_body { max_size 2MB }`, which is enforced first, on wire bytes (`.env.example` default). |
| `ADMIN_TOKEN` | **Y** | `.env` (SSM `/sentinelbrief/ADMIN_TOKEN`) | Bearer token for `POST /api/v1/alerts/{id}/retriage` (PRD §8, from M8); generate as `INGEST_HMAC_SECRET`; fetched now so the M8 release needs no new parameter. |
| `CORS_ORIGINS` | N — **pinned**, `api` only | compose (pinned) | `https://sentinelbrief.tyagiakanksha.com` — no wildcard, ever, in a deployed environment. |
| `ALERTS_LIST_CACHE_TTL_S` | N | default | `15` seconds (`.env.example` default). |
| `STATS_CACHE_TTL_S` | N | default | `60` seconds (`.env.example` default). |
| `STREAM_HEARTBEAT_S` | N | default | N — default 15; the SSE heartbeat interval (M8a); not set in the prod compose |
| `STREAM_MAX_CLIENTS` | N | default | N — default 50 per api process; concurrent SSE clients cap (M8a); not set in the prod compose |
| `ALERTS_CACHE_MAX_ENTRIES` | N | default | `1024` (`.env.example` default). |
| `TOOL_RESULT_MAX_CHARS` | N | default | `4000` (`.env.example` default). |
| `ASSETS_YAML_PATH` | N | compose (pinned) | `honeypot/assets.yaml` |
| `TOOL_SESSION_COMMANDS_MAX` | N | default | `40` (`.env.example` default). |
| `TOOL_SESSION_DOWNLOADS_MAX` | N | default | `10` (`.env.example` default). |
| `TOOL_COMMAND_MAX_CHARS` | N | default | `200` (`.env.example` default). |
| `MAXMIND_LICENSE_KEY` | **Y**, deploy-time only — never rendered into `.env` | read inline at deploy time (SSM) | Your MaxMind license key. Read inline by the geoip one-off (`prod/README.md`); never a container env var at runtime. |
| `GEOIP_DB_PATH` | N | compose (pinned) | `infra/geoip/GeoLite2-Country.mmdb` |
| `GEOIP_ASN_DB_PATH` | N | compose (pinned) | `infra/geoip/GeoLite2-ASN.mmdb` |
| `ABUSEIPDB_API_KEY` | **Y**, optional | `.env` (SSM, optional) | Your AbuseIPDB key — empty means `lookup_ip_reputation` always answers `unavailable`. |
| `ABUSEIPDB_CACHE_TTL_S` | N | default | `86400` seconds (`.env.example` default). |
| `ABUSEIPDB_TIMEOUT_S` | N | default | `5` seconds (`.env.example` default). |
| `ABUSEIPDB_MAX_AGE_DAYS` | N | default | `90` (`.env.example` default). |
| `ABUSEIPDB_CACHE_MAX_ENTRIES` | N | default | `4096` (`.env.example` default). |
| `ABUSEIPDB_QUOTA_BACKOFF_S` | N | default | `900` seconds (`.env.example` default). |
| `ALERT_HISTORY_MAX_WINDOW_HOURS` | N | default | `720` hours (`.env.example` default). |
| `TOOL_LOOP_MAX_ITER` | N | default | `6` (`.env.example` default). |
| `TRIAGE_JOB_MAX_TRIES` | N | default | `3` (`.env.example` default). |
| `TRIAGE_JOB_BACKOFF_BASE_S` | N | default | `2` seconds (`.env.example` default). |
| `TRIAGE_JOB_BACKOFF_MAX_S` | N | default | `60` seconds (`.env.example` default). |
| `FORWARDED_ALLOW_IPS` | N — **`*` in this topology** | compose (pinned), `api` only | Not a `Settings` field — uvicorn reads it natively (`--proxy-headers`, `infra/Dockerfile.api`'s `CMD`). Safe here ONLY because Caddy overwrites `X-Forwarded-For` AND `api` is never host-published (PRD §10.10). |

## `postgres`

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `POSTGRES_USER` | N | compose (pinned) | `sentinel` |
| `POSTGRES_DB` | N | compose (pinned) | `sentinelbrief` |
| `POSTGRES_PASSWORD` | **Y** | `.env.postgres` (SSM) | The same password `DATABASE_URL` above embeds. Rendered into its own file so `postgres` never loads the app's other secrets via `env_file`. |

## `web` — build-time

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | N — **pinned**, **build-time only** | `push_ecr.sh` build arg | `https://api.sentinelbrief.tyagiakanksha.com` — baked into the image at `next build` time (its `API_PUBLIC_URL` env var). Do **not** set this as a runtime env var — it has no effect once the image is built. |
| `API_URL` | N — **pinned**, **build-time AND runtime** | `push_ecr.sh` build arg, also compose (pinned) at runtime | `http://api:8000` — the compose-network origin. |

## `web` — runtime

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `HOSTNAME` | N — **pinned** | compose (pinned) | `0.0.0.0` |
| `PORT` | N — **pinned** | compose (pinned) | `3000` |
| `API_URL` | N — **pinned** | compose (pinned) | `http://api:8000` — see the build-time row above; needed again at runtime for server-side per-request calls. |

No other variables — the `web` container never receives a backend secret (`env_file` is set on
`api`/`worker` only, never `web`).

## `caddy`

No environment variables — the Caddyfile (`/opt/sentinelbrief/Caddyfile`, bind-mounted read-only)
is config, not env; `caddy_data`/`caddy_config` are named volumes for its ACME state, not secrets.

## Honeypot host's shipper

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `SHIPPER_INGEST_URL` | N — **pinned** | `/etc/sentinelbrief-shipper.env` (root:root, 600) on the honeypot host | `https://api.sentinelbrief.tyagiakanksha.com/api/v1/alerts` |
| `INGEST_HMAC_SECRET` | **Y** — shared with `api` | same file | Same value as the `api`/`worker` row above — the honeypot host's *only* shared secret with the app host. |
| `SHIPPER_MAX_PAYLOAD_BYTES` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `1500000` bytes — serialized payload byte cap; stays below `INGEST_MAX_BODY_BYTES` above (m6 task-02) and Caddy's 2 MB. |
| `SHIPPER_LOG_PATH` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `/opt/sentinelbrief-honeypot/data/log/cowrie.json` — the Cowrie JSON log to tail. |
| `SHIPPER_STATE_DIR` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `/var/lib/sentinelbrief-shipper` — tail position, spool, and `spool/dead/`. |
| `SHIPPER_IDLE_FLUSH_S` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `900` seconds — ship a never-closed session after this many idle seconds. |
| `SHIPPER_MAX_EVENTS` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `2000` — per-session event cap. |
| `SHIPPER_POST_TIMEOUT_S` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `10` seconds — per-POST HTTP timeout. |
| `SHIPPER_BACKOFF_BASE_S` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `2` seconds — delay before the first retry after a failed POST; doubles per consecutive failure. |
| `SHIPPER_BACKOFF_MAX_S` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `300` seconds — cap on the retry delay. |
| `SHIPPER_SPOOL_MAX_FILES` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `10000` — disk-protection cap; the OLDEST spooled payload is dropped once exceeded. |
| `SHIPPER_POLL_INTERVAL_S` | N | `/etc/sentinelbrief-shipper.env` (optional override) | `1` second — sleep between polls when the log has no new complete line. |

`honeypot/shipper/README.md` is the shipper's own reference: its `ShipperConfig` table is the
authority on every `SHIPPER_*` default and meaning, kept in sync with `ShipperConfig` itself.

## Host (root) — backup timer

Read by `/opt/sentinelbrief/backup.sh` and `/opt/sentinelbrief/restore-rehearsal.sh`, run as root
by `sentinelbrief-backup.timer` — outside the compose network, no container `env_file` involved.

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `BACKUP_S3_BUCKET` | N | `/opt/sentinelbrief/backup.env` (committed, `infra/deploy/prod/backup.env`) | `sentinelbrief-backups-181040156847` — the real S3 bucket name; instance-specific but not a credential (`infra/deploy/database.md`). |

## Dev/test only

| Variable | Secret? | Where it is set | Value / source |
|---|---|---|---|
| `TEST_DATABASE_URL` | N — never set on a deployed service | dev/CI shell only | Throwaway-schema Postgres URL for the test suite (CONVENTIONS.md §10). |
| `TEST_REDIS_URL` | N — never set on a deployed service | dev/CI shell only | Dedicated-instance Redis URL for the Redis test fixtures. |

## Secure-transport note

Every deployed hostname here (`sentinelbrief.`, `api.sentinelbrief.`) is HTTPS-only via Caddy's
Let's Encrypt certificates (`docs/deployment.md`); `ENVIRONMENT=production` above is what turns on
the api's boot-time required-secret guards.
