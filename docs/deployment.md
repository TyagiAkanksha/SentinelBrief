# Deployment — Phase 1 target topology (doc of record)

**Status:** finalized at M6 (task-05) with the real resource names below. From here on this file
is the doc of record: the box must match this file and the committed copies under
`infra/deploy/prod/`, or the drift is committed back in the same sitting.

It is the same single-host shape the author's AdvisorDesk runs in production (live since
2026-08-09), with the differences listed at the end.

## Why single-host

- AWS App Runner has been closed to new AWS customers since 2026-04-30. Always-on Fargate + ALB
  for six services prices at roughly 2–3× a single small instance.
- One small instance (t3.small, ~$15/month all-in; t3.medium if the six containers are
  memory-bound) runs everything behind Caddy. Phase 2 (Terraform → ECS Fargate, RDS) stays an
  optional M9 after 2+ weeks of uptime — the migration *story* is the point, not the migration.

## Topology

```
Cloudflare DNS (grey-cloud A records → Elastic IP)
        │
   EC2 t3.small (Amazon Linux 2023, inbound 80/443 only, no SSH — SSM only)
        │
      Caddy (auto-HTTPS via Let's Encrypt)
        ├── sentinelbrief.<domain>      → web container    :3000
        └── api.sentinelbrief.<domain>  → api container    :8000
                │
        ┌───────┴────────┬─────────────┐
      worker           postgres        redis
    (ARQ, LLM)        (named volume)  (queue/pubsub)

  + host systemd timer: sentinelbrief-backup (pg_dump → S3) — not a container

Honeypot EC2 (separate VPC/account, SSM only, Cowrie on :22) ──HTTPS POST──► api.sentinelbrief.<domain>/api/v1/alerts
```

- **DNS:** two A records (`sentinelbrief`, `api.sentinelbrief`) point at the instance's Elastic
  IP, both **DNS-only (grey cloud — never Proxied)**: Cloudflare's proxy buffers SSE and adds a
  redundant TLS hop. Re-check each record's proxy icon after saving — the form is known to
  silently reflow to Proxied.
- **TLS:** Caddy obtains and renews Let's Encrypt certificates for both hostnames automatically
  once DNS resolves; nothing to configure in AWS.
- **AWS resources** (us-east-1): the instance, its Elastic IP, a security group allowing inbound
  80/443 only, an IAM instance role/profile granting `AmazonSSMManagedInstanceCore` (management —
  there is no SSH port), `AmazonEC2ContainerRegistryReadOnly` (image pulls), an inline policy
  reading `/sentinelbrief/*` SSM parameters (+ `kms:Decrypt` via ssm), and `s3:PutObject` on the
  backup bucket. Concrete resource names are recorded in the table below, filled in by the owner
  at deploy time (`infra/deploy/ec2-single-host.md` step 12).

## Resources (recorded at deploy)

| Resource | Value |
|---|---|
| Region | `<recorded at deploy>` |
| AWS account id | `<recorded at deploy>` |
| App-host VPC id | `<recorded at deploy>` |
| Honeypot VPC id | `<recorded at deploy>` |
| App-host instance id | `<recorded at deploy>` |
| Honeypot instance id | `<recorded at deploy>` |
| App-host Elastic IP | `<recorded at deploy>` |
| Honeypot Elastic IP | `<recorded at deploy>` |
| App-host security group | `<recorded at deploy>` |
| Honeypot security group | `<recorded at deploy>` |
| App-host IAM role | `<recorded at deploy>` |
| Honeypot IAM role | `<recorded at deploy>` |
| S3 backup bucket | `<recorded at deploy>` |
| ECR repositories | `<recorded at deploy>` |
| Domain | `<recorded at deploy>` |

## Secrets: SSM Parameter Store, never in the repo or chat

Secret-bearing values live as SSM `SecureString` parameters under `/sentinelbrief/`:
`DATABASE_URL`, `LLM_API_KEY`, `INGEST_HMAC_SECRET`, `ADMIN_TOKEN`, `ABUSEIPDB_API_KEY`
(optional), `POSTGRES_PASSWORD`, `MAXMIND_LICENSE_KEY` (deploy-time only — used to fetch the
GeoLite2 `.mmdb` into a volume; the file itself is never committed or baked into an image). The
owner writes them from their own terminal; the instance role reads + decrypts them. On the box,
`/opt/sentinelbrief/fetch-secrets.sh` renders **both** files: the api/worker secrets into
`/opt/sentinelbrief/.env`, and `POSTGRES_PASSWORD` alone into its own
`/opt/sentinelbrief/.env.postgres` (both mode 600; prints only counts, never a value). All
**non-secret pinned values** (`ENVIRONMENT=production`, `CORS_ORIGINS`, model ids, thresholds,
`REDIS_URL`, `API_URL`) live in the production compose file (`REDIS_URL` is a non-secret here — no
password on the compose network — even though `Settings` types it `SecretStr`). Caddy's
`request_body { max_size 2MB }` (`infra/deploy/prod/Caddyfile`) parses to the same 2,000,000 bytes
as `INGEST_MAX_BODY_BYTES` and is enforced first, on wire bytes, before the api's own check ever
runs — "outer bound" means enforced first, not larger; never raise the app cap above it.
`infra/deploy/env-checklist.md` (M6) is the authority on every variable: which service needs it,
whether it is a secret, where its value comes from.

## On the box

Everything lives in `/opt/sentinelbrief/`: the production `docker-compose.yml` (six services:
`caddy`, `web`, `api`, `worker`, `postgres`, `redis` — backups are a host-level systemd timer, not
a service), the `Caddyfile`, `fetch-secrets.sh`, and the two rendered files (`.env`,
`.env.postgres`) — `.env` is loaded by `api` and `worker` only, `.env.postgres` by `postgres` only;
the `web` container gets no backend secrets. Deploy/redeploy cycle, via an SSM session:

1. `aws ecr get-login-password | docker login …` (the instance role authorizes the pull).
2. `./fetch-secrets.sh` (only when a parameter changed).
3. `docker compose pull api && docker compose run --rm api uv run alembic upgrade head` when the
   release carries a migration — **before** `up -d`, never via `exec` into the still-running old
   container; see `infra/deploy/prod/README.md` for the exact commands and why the order matters.
4. `docker compose pull && docker compose up -d`.

The GeoLite2 `.mmdb` files are fetched once, deploy-time, as a one-off — never baked into the
image or run automatically at container start: `MAXMIND_LICENSE_KEY` is read from SSM inline (it
is never rendered into a file by `fetch-secrets.sh`) and passed only to a throwaway container that
writes into the `/opt/sentinelbrief/geoip` volume the `api`/`worker` services mount read-only
(`infra/deploy/prod/README.md` has the exact command).

Images are built locally by `infra/deploy/push_ecr.sh`, tagged `latest` **and** the git short SHA;
the production compose file pins the **SHA tags**, so a running service can never silently change
under a rebuild. Each redeploy updates the tag in both the box's copy and the committed copy under
`infra/deploy/prod/`, so at any point in time that file names exactly what is running.

## Forwarded-IP handling

The Caddyfile **overwrites** `X-Forwarded-For` with `{remote_host}` on every proxied request — a
client-supplied chain is discarded wholesale, so the header the API sees is spoof-proof by
construction. Because the `api` container is not host-published (only Caddy reaches it over the
compose network), `FORWARDED_ALLOW_IPS=*` on uvicorn is safe **in this topology and only because
of both properties together** (PRD §10.10). `infra/deploy/VERIFY.md` (M6) carries the live checks:
two real client IPs get separate rate-limit buckets, and forged-XFF requests after bucket
exhaustion stay `429`.

## Data: backups and retention

- Postgres data lives on a named volume on the instance's EBS root (or a dedicated EBS volume if
  growth warrants it).
- A host systemd timer, `sentinelbrief-backup.timer` (03:15 UTC, `Persistent=true`), runs
  `/opt/sentinelbrief/backup.sh` nightly: `pg_dump | gzip` through the running compose `postgres`
  service, uploaded to an S3 bucket with a 30-day lifecycle rule using the instance role — no
  extra image, no AWS credentials in a container. The restore procedure (`gunzip | psql`) and the
  rehearsal script (`restore-rehearsal.sh`) are documented in `infra/deploy/database.md` at M6 and
  rehearsed once.
- Honeypot traffic is voluminous. A retention policy for `alerts.raw` (e.g. keep 90 days of raw
  payloads, keep verdicts forever) is an owner decision recorded in `infra/deploy/database.md`
  before the 48 h M6 soak, not after the disk fills.

## Logs

No CloudWatch integration in Phase 1 — container stdout/stderr stays on the box. Every service in
the production compose file sets `logging: {driver: json-file, options: {max-size: "10m",
max-file: "3"}}` **from day one** (an AdvisorDesk follow-up that SentinelBrief starts with, because
honeypot traffic is chatty). Via SSM: `docker compose logs api --since 1h`. The M6 acceptance check
"no LLM call originates from any public request" is a grep over the `api` container's log for the
LLM client's log line, which must appear only in the `worker` container.

## Honeypot host

- The compose file is `honeypot/docker-compose.yml` (Cowrie only, host port 22 → container 2222,
  `honeypot/etc/cowrie.cfg` bind-mounted read-only). Before every deploy, re-check the image
  digest (`honeypot/README.md` step 4) and re-pin `honeypot/docker-compose.yml`'s `image:` line
  on the box and in the repo in the same sitting if it changed. The full owner-run runbook —
  instance, security group, user data, digest pin, start/verify, what must never be on the host —
  is `honeypot/README.md`.
- Cheapest instance (t3.nano / t4g.nano class) in a **separate VPC or account**. Assume it will be
  fully compromised — that is its job. It shares no credentials with the app host; its only
  secret is `INGEST_HMAC_SECRET`, and its instance role has SSM core permissions and nothing else.
- **SSM-only management, no `sshd` on any port**, so Cowrie (in Docker, `cowrie/cowrie`) owns
  port 22 outright. There is nothing to move to a high port.
- Outbound security group: 443 to the ingest hostname and to the SSM endpoints only.
- The shipper (`honeypot/shipper/`) runs as a systemd unit: tails `cowrie.json`, groups events by
  `session`, posts one signed alert on `cowrie.session.closed`, spools to local disk with
  exponential backoff when the ingest URL is unreachable, and drains the spool in order when it
  returns. Sessions that never close (Cowrie restart) are flushed after an idle timeout. Its one
  secret (`INGEST_HMAC_SECRET`) and the ingest URL live in `/etc/sentinelbrief-shipper.env`
  (root:root, mode 600); its working state — the tail position, the pending spool, and the
  dead-letter sink for a permanently rejected (`401`/`413`/`422`) payload — lives under
  `/var/lib/sentinelbrief-shipper` (`tail.json`, `spool/`, `spool/dead/`), owned by the
  unprivileged `shipper` user and created by systemd's `StateDirectory=` directive.
- `honeypot/assets.yaml` describes the fleet (role, exposure, criticality) for `get_asset_info`.

## Verification

`infra/deploy/VERIFY.md` (M6) is the checklist, in the AdvisorDesk shape: every check is the exact
command to run against the live stack with a block to paste the real output into. It runs checks
0–11: `/healthz`, TLS + security headers, the ingest gates (incl. the body-cap semantics), a
signed POST from the honeypot host reaching `triaged`, CORS allow/deny, the dashboard, the "no LLM
in api logs" grep, backups (S3 object + restore rehearsal + `pg_database_size`), log rotation,
honeypot hardening, Redis degrade/recover, and (from M8) rate limits from two real IPs plus the
forged-XFF check and SSE arriving unbuffered through the custom domain.

## Differences from AdvisorDesk

| AdvisorDesk | SentinelBrief |
|---|---|
| Supabase Postgres (external) | Self-hosted Postgres in compose + nightly `pg_dump` to S3 (honeypot volume would blow through Supabase's free tier) |
| Three containers (api, admin, client) | Six (caddy, web, api, worker, postgres, redis) + a host systemd backup timer |
| Google OAuth admin app | No login anywhere; one admin bearer token for `retriage` only |
| In-memory rate limiter | Redis-backed rate limiter (survives restarts; Redis already present) |
| No second host | Isolated honeypot host, SSM-only, Cowrie on 22 |
| Log rotation staged as a follow-up | Log rotation from day one |
| `DATABASE_URL` override hack in compose | Not needed — Postgres is a fixed compose service |
