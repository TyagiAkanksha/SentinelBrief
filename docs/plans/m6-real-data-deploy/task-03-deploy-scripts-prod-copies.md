---
id: task-03
milestone: m6-real-data-deploy
depends_on: []
status: planned
spec: PRD.md §11 Phase 1 (one EC2 running caddy/web/api/worker/postgres/redis via compose; images built locally, pushed to ECR, tagged with the git SHA; the production compose pins those tags and is committed as a synced copy under `infra/deploy/prod/`; secrets in SSM `/sentinelbrief/*` rendered by `fetch-secrets.sh` into a root-only `.env`; non-secret pinned values in the compose file; log rotation on every service from day one; `NEXT_PUBLIC_API_URL` baked at build), §10.5 (secrets via env, `.env` gitignored), §10.10 (forwarded-IP trust needs BOTH Caddy's XFF overwrite AND a never-host-published api), §8 (`/healthz` at the root; SSE from M8 must not be buffered), §13 (working assumption: AWS, t3.small, `sentinelbrief.tyagiakanksha.com`); `docs/deployment.md` (doc of record — the sections this task makes concrete: Secrets, On the box, Forwarded-IP handling, Logs); `docs/plans/m6-real-data-deploy.md` Global Constraints (Caddy `request_body { max_size 2MB }`; git-SHA tags, never `latest`; prod copies equal the box; json-file rotation everywhere; the M5 carried items: `docs/deployment.md:57` `REDIS_URL` classification, N-W1 restart policies in BOTH compose files, `LLM_TIMEOUT_S` for the `worker/llm_client.py:94` literal); `CONVENTIONS.md` §7, §11; `.claude/rules/infra.md`; `.claude/rules/core.md`; AdvisorDesk `infra/deploy/{push_ecr.sh,prod/docker-compose.yml,prod/Caddyfile,prod/fetch-secrets.sh,prod/README.md,env-checklist.md}` as the templates
---

# task-03 — `infra/deploy/`: `push_ecr.sh`, the production compose file (six services), the Caddyfile, `fetch-secrets.sh`, `env-checklist.md`, static pin tests; plus the three carried M5 items (restart policies, `REDIS_URL` classification, `LLM_TIMEOUT_S`)

## Goal

Everything the app host runs is written down here, reviewed here, and pinned by tests here before
it ever touches the box: a build-and-push script that tags both images with the git short SHA; a
production compose file with exactly six services (`caddy`, `web`, `api`, `worker`, `postgres`,
`redis`) where only Caddy publishes a port, `api` and `worker` load the rendered `.env`, `postgres`
loads its own one-variable env file, every service restarts and rotates its logs, and no image tag
is `latest`; a Caddyfile that terminates TLS for the two hostnames, overwrites `X-Forwarded-For`,
caps the api's request body at 2 MB and streams responses unbuffered; a secrets script that renders
SSM parameters into two root-only files and prints only counts; and an environment checklist that
names every variable each service needs, whether it is a secret, and where its value comes from.
`infra/deploy/prod/*` are synced copies — after the first deploy (task-05) they carry the real
account id and SHA, and the pin tests keep them honest. Three M5 carry-overs land here because
this is where the production roster is written: `restart:` policies in both compose files (walk
finding N-W1), `REDIS_URL` documented as a non-secret pinned compose value even though `Settings`
keeps it `SecretStr` (D12/N-M10), and the `worker/llm_client.py:94` `timeout=60.0` literal becoming
`LLM_TIMEOUT_S` so the checklist can list it.

## Context (read ONLY these)

- `PRD.md` §8 (`/healthz`, SSE note), §10.5, §10.10, §11, §13.
- `docs/deployment.md` (whole file — the shape you are making concrete; you also edit it).
- `docs/plans/m6-real-data-deploy.md` — Global Constraints.
- `.claude/rules/infra.md`; `CONVENTIONS.md` §7, §11.
- AdvisorDesk templates (read from `~/Documents/github_akanksha/AdvisorDesk/infra/deploy/` — copy
  the shape, not the names): `push_ecr.sh`, `prod/docker-compose.yml`, `prod/Caddyfile`,
  `prod/fetch-secrets.sh`, `prod/README.md`, `env-checklist.md`.
- Code you build on: `infra/docker-compose.yml` (the dev file — service shapes, healthchecks,
  `x-logging` anchor; you add `restart:` lines), `infra/Dockerfile.api` (you add `COPY scripts
  ./scripts` to BOTH stages so `scripts/fetch_geoip.py` can run on the box in a one-off container
  — the box has no repo checkout), `infra/Dockerfile.web` (build args `NEXT_PUBLIC_API_URL`,
  `API_URL`), `tests/test_compose_config.py` (the `_render_compose_config` tmp-copy pattern and
  `_ENV_FILE_LEAK_CANARY`), `tests/test_dockerfile_pins.py` (`_instructions()`,
  `_runtime_copy_from_builder`), `core/config.py::Settings` (the roster the checklist must cover
  — every field, uppercased), `worker/llm_client.py::from_settings` (line 94: `timeout=60.0`),
  `tests/test_llm_client.py::test_from_settings_happy_path_configures_client` (how the built
  `AsyncOpenAI` is inspected — `client._client.timeout` is a plain `12.5` float when constructed
  with `timeout=12.5`, verified against the installed SDK at briefing), `tests/test_config.py`,
  `tests/test_env_example_roster.py`, `.env.example`, `scripts/fetch_geoip.py` (its CLI:
  `--out-dir DIR`; reads `MAXMIND_LICENSE_KEY` from the environment).
- Domain (PRD §13 working assumption, owner to confirm at task-05): `sentinelbrief.tyagiakanksha.com`
  and `api.sentinelbrief.tyagiakanksha.com`. If the owner picks another domain, the change is one
  `sed` over the hits of `grep -rn "sentinelbrief.tyagiakanksha.com" infra/deploy docs/deployment.md
  README.md` — every one of them is listed by the implementer's report (rule 11).

## Files

- Create: `infra/deploy/push_ecr.sh` (executable), `infra/deploy/prod/docker-compose.yml`,
  `infra/deploy/prod/Caddyfile`, `infra/deploy/prod/fetch-secrets.sh` (executable),
  `infra/deploy/prod/README.md`, `infra/deploy/env-checklist.md`
- Create (test-author): `tests/test_prod_compose.py`, `tests/test_prod_caddyfile.py`,
  `tests/test_deploy_scripts.py`, `tests/test_env_checklist.py`; extend
  `tests/test_compose_config.py` (`test_compose_every_service_has_restart_on_failure`),
  `tests/test_dockerfile_pins.py` (`test_scripts_copied_for_deploy_time_geoip_fetch`),
  `tests/test_llm_client.py` (`test_from_settings_uses_llm_timeout_s`), `tests/test_config.py`
  (`test_llm_timeout_s_default`)
- Modify: `infra/docker-compose.yml` (five `restart: on-failure` lines), `infra/Dockerfile.api`
  (`COPY scripts ./scripts` in the builder; `COPY --from=builder --chown=appuser:appuser
  /app/scripts ./scripts` in the runtime), `core/config.py` (`llm_timeout_s`), `worker/llm_client.py`
  (line 94 → `timeout=settings.llm_timeout_s`), `.env.example` (`LLM_TIMEOUT_S=60` under the LLM
  block, with the sizing sentence), `docs/deployment.md` (Secrets: the two rendered files and the
  `REDIS_URL` sentence at line 57; On the box: the geoip one-off; Logs: unchanged), `CONVENTIONS.md`
  §7 (one sentence: `REDIS_URL` is `SecretStr` because it MAY carry a password; in this deployment
  it carries none and is pinned in the compose file), `README.md` (Deployment section: the
  `infra/deploy/` file list)

## Interfaces

- **Consumes:** `Settings` field names; `infra/Dockerfile.*` build args; `scripts/fetch_geoip.py`
  CLI; task-02's `INGEST_MAX_BODY_BYTES` (the Caddy cap is the outer bound of it).
- **Produces (tasks 04–06 rely on — produce exactly):**

  ```bash
  # infra/deploy/push_ecr.sh — AdvisorDesk's shape, two repos
  # env: AWS_ACCOUNT_ID (required; 12 digits — fails fast), AWS_REGION (default us-east-1),
  #      API_PUBLIC_URL (default https://api.sentinelbrief.tyagiakanksha.com), BUILD_PLATFORM (default linux/amd64)
  # ECR repos: sentinelbrief/api (infra/Dockerfile.api, no build args), sentinelbrief/web (infra/Dockerfile.web,
  #            --build-arg NEXT_PUBLIC_API_URL=$API_PUBLIC_URL --build-arg API_URL=http://api:8000)
  # Builds once with -t :latest -t :$GIT_SHA (git rev-parse --short HEAD), ensure_repo idempotent, pushes both tags,
  # prints the two :$GIT_SHA URIs and "Next: infra/deploy/ec2-single-host.md".  Repo root resolved from $BASH_SOURCE.
  ```

  ```yaml
  # infra/deploy/prod/docker-compose.yml  (lives at /opt/sentinelbrief/docker-compose.yml on the box)
  name: sentinelbrief
  x-logging: &default-logging { driver: json-file, options: { max-size: "10m", max-file: "3" } }
  x-shared-env: &shared-env            # non-secret pinned values (PRD §11) — identical for api and worker
    ENVIRONMENT: production
    REDIS_URL: redis://redis:6379/0    # no password on the compose network (docs/deployment.md "Secrets"); Settings keeps it SecretStr because it MAY carry one elsewhere
    LLM_BASE_URL: https://api.openai.com/v1
    CHEAP_MODEL: gpt-4o-mini
    STRONG_MODEL: gpt-5.4
    MODEL_PRICES_JSON: '{"gpt-4o-mini":{"input_per_mtok":"0.15","output_per_mtok":"0.60"},"gpt-5.4":{"input_per_mtok":"2.50","output_per_mtok":"15.00"}}'
    TRIAGE_PROMPT_VERSION: triage-v4
    GEOIP_DB_PATH: infra/geoip/GeoLite2-Country.mmdb
    GEOIP_ASN_DB_PATH: infra/geoip/GeoLite2-ASN.mmdb
    ASSETS_YAML_PATH: honeypot/assets.yaml
  services:
    caddy:
      image: caddy:2
      ports: ["80:80", "443:443"]                                     # the ONLY host-published ports on the box
      volumes: ["/opt/sentinelbrief/Caddyfile:/etc/caddy/Caddyfile:ro", "caddy_data:/data", "caddy_config:/config"]
      depends_on: [web, api]
      restart: unless-stopped
      logging: *default-logging
    web:
      image: 000000000000.dkr.ecr.us-east-1.amazonaws.com/sentinelbrief/web:0000000   # placeholder until the first push (task-05 replaces both tags in the same sitting)
      environment: { HOSTNAME: 0.0.0.0, PORT: "3000", API_URL: http://api:8000 }        # no env_file — the web container never sees a backend secret
      expose: ["3000"]
      depends_on: { api: { condition: service_healthy } }
      restart: unless-stopped
      logging: *default-logging
    api:
      image: 000000000000.dkr.ecr.us-east-1.amazonaws.com/sentinelbrief/api:0000000
      env_file: [/opt/sentinelbrief/.env]
      environment:
        <<: *shared-env
        CORS_ORIGINS: https://sentinelbrief.tyagiakanksha.com
        FORWARDED_ALLOW_IPS: "*"        # safe ONLY because Caddy overwrites X-Forwarded-For AND this service is never host-published (PRD §10.10)
      expose: ["8000"]
      volumes: ["/opt/sentinelbrief/geoip:/app/infra/geoip:ro"]
      depends_on: { postgres: { condition: service_healthy }, redis: { condition: service_healthy } }
      restart: unless-stopped
      logging: *default-logging
    worker:
      image: 000000000000.dkr.ecr.us-east-1.amazonaws.com/sentinelbrief/api:0000000     # same image + tag as api
      command: ["arq", "worker.main.WorkerSettings"]
      env_file: [/opt/sentinelbrief/.env]
      environment: { <<: *shared-env }
      volumes: ["/opt/sentinelbrief/geoip:/app/infra/geoip:ro"]
      depends_on: { postgres: { condition: service_healthy }, redis: { condition: service_healthy } }
      healthcheck: { test: ["CMD", "arq", "--check", "worker.main.WorkerSettings"], interval: 30s, timeout: 10s, start_period: 20s, retries: 3 }
      restart: unless-stopped
      logging: *default-logging
    postgres:
      image: postgres:16
      env_file: [/opt/sentinelbrief/.env.postgres]                   # POSTGRES_PASSWORD only — never the app's secrets
      environment: { POSTGRES_USER: sentinel, POSTGRES_DB: sentinelbrief }
      volumes: ["sentinelbrief_pg:/var/lib/postgresql/data"]
      healthcheck: { test: ["CMD-SHELL", "pg_isready -U sentinel -d sentinelbrief"], interval: 5s, timeout: 3s, retries: 10 }
      restart: unless-stopped
      logging: *default-logging
    redis:
      image: redis:7-alpine
      command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]   # NO maxmemory / eviction policy (M5 ruling R14: ARQ keys carry expiries; eviction would drop queued jobs)
      volumes: ["sentinelbrief_redis:/data"]
      healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: 5s, timeout: 3s, retries: 10 }
      restart: unless-stopped
      logging: *default-logging
  volumes: { sentinelbrief_pg: {}, sentinelbrief_redis: {}, caddy_data: {}, caddy_config: {} }
  # NO build: sections. NO ports: on web/api/worker/postgres/redis. No `latest` anywhere.
  ```

  ```caddyfile
  # infra/deploy/prod/Caddyfile  (/opt/sentinelbrief/Caddyfile on the box)
  api.sentinelbrief.tyagiakanksha.com {
  	header {
  		Strict-Transport-Security "max-age=31536000; includeSubDomains"
  		X-Content-Type-Options "nosniff"
  		Referrer-Policy "strict-origin-when-cross-origin"
  		X-Frame-Options "DENY"
  		Content-Security-Policy "frame-ancestors 'none'"
  	}
  	request_body {
  		max_size 2MB           # outer bound of the api's INGEST_MAX_BODY_BYTES (task-02); the honeypot host is assumed compromised
  	}
  	reverse_proxy api:8000 {
  		header_up X-Forwarded-For {remote_host}    # OVERWRITE, never append (PRD §10.10)
  		flush_interval -1                          # GET /api/v1/stream (M8) must reach the browser unbuffered
  	}
  }
  sentinelbrief.tyagiakanksha.com {
  	header { …same five headers… }
  	reverse_proxy web:3000
  }
  ```

  ```bash
  # infra/deploy/prod/fetch-secrets.sh — runs ON THE BOX as root via the instance role; prints counts only
  set -euo pipefail; REGION=us-east-1; umask 077
  OUT=/opt/sentinelbrief/.env            # DATABASE_URL LLM_API_KEY INGEST_HMAC_SECRET ADMIN_TOKEN (required) + ABUSEIPDB_API_KEY (optional: absent parameter → written empty, one "optional parameter absent" line naming it)
  OUT_PG=/opt/sentinelbrief/.env.postgres  # POSTGRES_PASSWORD (required) — the same password DATABASE_URL embeds (env-checklist.md says so)
  # aws ssm get-parameter --region "$REGION" --name "/sentinelbrief/$P" --with-decryption --query Parameter.Value --output text
  # chmod 600 both; echo "OK wrote N vars to $OUT and 1 var to $OUT_PG".  MAXMIND_LICENSE_KEY is NOT rendered — it is read inline by the geoip one-off (prod/README.md) and never lands in a file.
  ```

  `infra/deploy/prod/README.md` — AdvisorDesk's "synced copies" doc adapted: source of truth is
  the box; change procedure (edit here → review → apply via SSM → `VERIFY.md` → commit drift);
  **migrations**: `docker compose pull api && docker compose run --rm api uv run alembic upgrade
  head` from the NEW image BEFORE `docker compose up -d` (never `exec` into the old container);
  **image tags**: bump both `web` and `api`/`worker` to the same SHA in both copies; **geoip
  one-off**: `MAXMIND_LICENSE_KEY="$(aws ssm get-parameter --region us-east-1 --name
  /sentinelbrief/MAXMIND_LICENSE_KEY --with-decryption --query Parameter.Value --output text)"
  docker compose run --rm -e MAXMIND_LICENSE_KEY -v /opt/sentinelbrief/geoip:/app/infra/geoip api
  uv run python scripts/fetch_geoip.py --out-dir infra/geoip` (the rw mount override is the one-off
  only; the services keep `:ro`); **secrets rotation**: update SSM → `./fetch-secrets.sh` →
  `docker compose up -d api worker` (and `postgres` only if `POSTGRES_PASSWORD` changed — which also
  needs `ALTER USER` inside the DB; documented).

  `infra/deploy/env-checklist.md` — one table per consumer (`api`, `worker`, `postgres`, `web`
  build-time, `web` runtime, `caddy`, the honeypot host's shipper), columns `Variable | Secret? |
  Where it is set | Value / source`. Every `core.config.Settings` field (uppercased) appears
  exactly once across the `api`/`worker` tables ("default" is a valid "where"); plus
  `FORWARDED_ALLOW_IPS`, `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, `HOSTNAME`, `PORT`,
  `NEXT_PUBLIC_API_URL`, `API_URL`, `SHIPPER_INGEST_URL` and the `SHIPPER_*` tunables (task-02).
  `REDIS_URL`'s row: "N — pinned in compose (`redis://redis:6379/0`, no password on the compose
  network); `Settings` keeps it `SecretStr` because a URL MAY embed a password". `TEST_*` rows say
  "never set on a deployed service". No value-shaped secret anywhere (pinned).

  **Carried M5 items:**
  - `infra/docker-compose.yml`: every service gains `restart: on-failure` (**ruling:** the dev
    file uses `on-failure`, not `unless-stopped` — N-W1's failure (the ARQ worker exiting 1 when
    Redis vanished) is covered, without auto-starting six containers at every dev-machine boot;
    prod uses `unless-stopped`. Cost if wrong: one word per service).
  - `core/config.py`: `llm_timeout_s: Annotated[float, Field(gt=0)] = 60.0`; `worker/llm_client.py`
    line 94: `timeout=settings.llm_timeout_s`; `.env.example`: `LLM_TIMEOUT_S=60` — "Per-request
    HTTP timeout for one LLM call, in seconds (the SDK retries up to 2 times inside one
    `complete_*` call, so one call can take up to 3× this). Size `TRIAGE_ATTEMPT_TIMEOUT_S` above
    the tool loop's worst case: (TOOL_LOOP_MAX_ITER + 4) calls × this value is the theoretical
    bound; the defaults (10 × 60 s vs 100 s) rely on the attempt deadline cutting a pathological
    loop, which is the intended backstop (M5 ruling R15)."
  - `docs/deployment.md:57` → the sentence "`REDIS_URL` … live in the production compose file"
    stays, and gains "(`REDIS_URL` is a non-secret here — no password on the compose network —
    even though `Settings` types it `SecretStr`)"; the Secrets section names BOTH rendered files.

## Interfaces → test table

`tests/test_prod_compose.py` renders `infra/deploy/prod/docker-compose.yml` from a tmp copy in
which every literal `/opt/sentinelbrief/` is rewritten to `<tmp>/opt/sentinelbrief/` (the test
creates that directory with `.env` = `_ENV_FILE_LEAK_CANARY`-style line `SENTINELBRIEF_TEST_CANARY=canary`,
`.env.postgres` = `SENTINELBRIEF_PG_CANARY=pgcanary`, an empty `Caddyfile`, an empty `geoip/`),
then `docker compose -f <copy> config --format json` (skip by name without `docker`).

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| six services, validates | `test_prod_compose.py::test_prod_compose_validates_with_exactly_six_services` | `set(services) == {"caddy","web","api","worker","postgres","redis"}` (task-04 does NOT add a service — backups are a host timer) |
| only caddy publishes | `::test_only_caddy_publishes_ports_80_and_443` | caddy `published` ∈ {"80","443"} exactly; every other service `not get("ports")` |
| env_file routing | `::test_env_file_reaches_api_and_worker_only_and_pg_gets_its_own` | canary in `api.environment` and `worker.environment`; absent from `web`, `caddy`, `redis`, `postgres`; `pgcanary` present in `postgres` and absent from the other five |
| shared env | `::test_api_and_worker_share_the_pinned_non_secret_env` | for each key in `x-shared-env` (the test lists them): `api.environment[k] == worker.environment[k]`; `api.environment["ENVIRONMENT"] == "production"`; `REDIS_URL == "redis://redis:6379/0"`; `CORS_ORIGINS` startswith `https://` and has no `*`; `FORWARDED_ALLOW_IPS == "*"` present on api only |
| images | `::test_images_are_ecr_sha_tags_and_api_worker_share_one` | regex `^\d{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com/sentinelbrief/(api\|web):[0-9a-f]{7,40}$` on web/api/worker; `api.image == worker.image`; web's tag == api's tag; `"latest"` in no image string; `"build" not in svc` for every service |
| restart + logging | `::test_every_service_restarts_unless_stopped_and_rotates_logs` | all six: `restart == "unless-stopped"`, logging json-file `10m`/`3` |
| worker shape | `::test_worker_shape` | command `["arq","worker.main.WorkerSettings"]`; healthcheck contains `arq --check`; depends_on postgres+redis `service_healthy`; geoip mount `read_only` |
| redis no eviction | `::test_redis_has_no_maxmemory_and_snapshots` | command == the dev file's list; `"maxmemory" not in " ".join(command)`; named volume |
| postgres | `::test_postgres_named_volume_and_healthcheck` | `sentinelbrief_pg` mounted at `/var/lib/postgresql/data`; `pg_isready` |
| caddy mounts | `::test_caddy_mounts_caddyfile_read_only_and_persists_data` | bind `…/Caddyfile` → `/etc/caddy/Caddyfile` `read_only`; `caddy_data:/data` |
| web env | `::test_web_environment_is_exactly_hostname_port_api_url` | `set(web.environment) == {"HOSTNAME","PORT","API_URL"}`; `API_URL == "http://api:8000"` |
| Caddyfile | `test_prod_caddyfile.py::test_two_hosts_xff_overwrite_body_cap_and_flush` | text: both hostnames present; api block (the text between `api.sentinelbrief…{` and the next top-level `}`) contains `header_up X-Forwarded-For {remote_host}`, `max_size 2MB`, `flush_interval -1`, `reverse_proxy api:8000`; web block `reverse_proxy web:3000`; both blocks contain `Strict-Transport-Security`; no line starts with `http://` or `:80` (no plaintext site); `"header_up X-Forwarded-For"` appears exactly once and never with `{http.request.header.X-Forwarded-For}` (append form) |
| push script | `test_deploy_scripts.py::test_push_ecr_shape` | `bash -n` exits 0; text has `AWS_ACCOUNT_ID` required guard (`ERROR: AWS_ACCOUNT_ID`), `--platform`, `sentinelbrief/api`, `sentinelbrief/web`, `git rev-parse --short HEAD`, `NEXT_PUBLIC_API_URL=`, `API_URL=http://api:8000`, `set -euo pipefail`; executable bit set (`os.access(X_OK)`) |
| secrets script | `::test_fetch_secrets_shape_never_echoes_values` | `bash -n`; `umask 077`; `chmod 600`; `--with-decryption`; the required parameter list == `{DATABASE_URL, LLM_API_KEY, INGEST_HMAC_SECRET, ADMIN_TOKEN}` and optional == `{ABUSEIPDB_API_KEY}` (parsed from the `for P in …` lines); `POSTGRES_PASSWORD` written to `.env.postgres`; `MAXMIND_LICENSE_KEY` absent; no `echo "$V"`/`printf … "$V"` to stdout (regex `echo[^\n]*\$V` and `>&2[^\n]*\$V` → 0 hits; the only `$V` use is the `printf '%s=%s\n' "$P" "$V" >> "$OUT"` line) |
| checklist roster | `test_env_checklist.py::test_every_settings_field_and_infra_var_listed_once` | for every `Settings` field: `` `NAME` `` appears in `env-checklist.md`; each of `FORWARDED_ALLOW_IPS`, `POSTGRES_PASSWORD`, `NEXT_PUBLIC_API_URL`, `API_URL`, `SHIPPER_INGEST_URL` present; `TEST_DATABASE_URL` and `TEST_REDIS_URL` present with "never set on a deployed service" on the same line |
| checklist no secrets | `::test_checklist_carries_no_value_shaped_secret` | regex over the file: no `sk-[A-Za-z0-9]{20,}`, no `postgresql://[^<\s]+:[^<\s]+@` (a real DSN with a password), no 32+-char hex/base64 runs outside code spans of `<…>` placeholders |
| dev restart | `test_compose_config.py::test_compose_every_service_has_restart_on_failure` | all five dev services `restart == "on-failure"` (rule 7: distinguishable from prod's `unless-stopped`) |
| Dockerfile scripts | `test_dockerfile_pins.py::test_scripts_copied_for_deploy_time_geoip_fetch` | builder has `COPY scripts ./scripts`; runtime `COPY --from=builder … /app/scripts ./scripts` with `--chown=appuser:appuser` |
| LLM timeout | `test_llm_client.py::test_from_settings_uses_llm_timeout_s` | `Settings(cheap_model=…, model_prices_json=…, llm_timeout_s=12.5)` → `client._client.timeout == 12.5` (rule 7) |
| LLM timeout default | `test_config.py::test_llm_timeout_s_default` | `Settings().llm_timeout_s == 60.0  # R17: the .env.example default, literal on purpose` |
| roster | existing `tests/test_env_example_roster.py` | `LLM_TIMEOUT_S` line required |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the four new files and the four extended
files; the **implementer** does Steps 3–7.

- [ ] **Step 1 (RED — test-author):** the four new test files + the four extensions per the table.
- [ ] **Step 2 (RED — test-author):** `uv run pytest -q tests/test_prod_compose.py
  tests/test_prod_caddyfile.py tests/test_deploy_scripts.py tests/test_env_checklist.py
  tests/test_compose_config.py tests/test_dockerfile_pins.py tests/test_llm_client.py
  tests/test_config.py` → Expected: the four new files fail on missing paths; the restart test
  fails (no `restart` key); the scripts-copy test fails; `llm_timeout_s` → `ValidationError`
  (unknown field). Pin, commit `test(infra): prod compose/Caddyfile/scripts/checklist pins +
  restart/LLM_TIMEOUT_S RED (m6 task-03)`.
- [ ] **Step 3 (GREEN — implementer): `push_ecr.sh`, `prod/docker-compose.yml`, `prod/Caddyfile`,
  `prod/fetch-secrets.sh`** per Interfaces; `bash -n` both scripts; `docker compose -f
  infra/deploy/prod/docker-compose.yml config > /dev/null` from a scratch dir with the two env
  files present (NEVER print the rendered config — `.claude/rules/infra.md`).
- [ ] **Step 4 (GREEN — implementer): `caddy validate`** — `docker run --rm -v
  "$PWD/infra/deploy/prod/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2 caddy validate --config
  /etc/caddy/Caddyfile --adapter caddyfile` → `Valid configuration`; paste the line.
- [ ] **Step 5 (GREEN — implementer): carried items** — dev compose `restart: on-failure` ×5;
  `Dockerfile.api` scripts copies (`docker compose -f infra/docker-compose.yml build api` still
  builds; paste the last line); `llm_timeout_s` + `.env.example` + `from_settings`.
- [ ] **Step 6 (implementer): docs** — `prod/README.md`, `env-checklist.md`, `docs/deployment.md`
  edits, `CONVENTIONS.md` §7 sentence, `README.md` Deployment file list.
- [ ] **Step 7 (implementer): full gates (cold) → commit** `feat(infra): push_ecr, prod compose +
  Caddyfile + fetch-secrets, env checklist; restart policies; LLM_TIMEOUT_S (m6 task-03)`;
  path-scoped `git add infra core/config.py worker/llm_client.py .env.example docs/deployment.md
  CONVENTIONS.md README.md`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_prod_compose.py tests/test_prod_caddyfile.py tests/test_deploy_scripts.py tests/test_env_checklist.py tests/test_compose_config.py tests/test_dockerfile_pins.py tests/test_llm_client.py tests/test_config.py tests/test_env_example_roster.py   # all pass, 0 skipped
grep -c "restart: on-failure" infra/docker-compose.yml            # 5   (BASE: 0)
grep -c "restart: unless-stopped" infra/deploy/prod/docker-compose.yml   # 6   (BASE: file absent → grep exits 2)
grep -n "timeout=60.0" worker/llm_client.py                        # no output, exit 1  (BASE: line 94)
grep -n "latest" infra/deploy/prod/docker-compose.yml              # no output, exit 1
git ls-files infra/deploy | grep -c '\.env'                        # 0
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
```

## Acceptance

- The production compose file, Caddyfile and secrets script exist as committed copies that render,
  validate and pass the pins: six services, one port-publishing service, secrets reaching only the
  containers that need them, git-SHA image tags, rotation and restart on every service, XFF
  overwrite + 2 MB body cap + unbuffered streaming on the api vhost.
- `push_ecr.sh` builds and pushes both images with `latest` + SHA tags for a stated account/region;
  `env-checklist.md` lists every variable every consumer needs with its secrecy and source and
  carries no value.
- The three carried M5 items are closed with pins: dev restart policies, `REDIS_URL`'s
  classification in the doc of record, and `LLM_TIMEOUT_S` replacing the last timeout literal.
