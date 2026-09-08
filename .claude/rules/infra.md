---
paths: infra/**, honeypot/**, docs/deployment.md
---

# Rules for `infra/`, `honeypot/`, and `docs/deployment.md`

- **No secret values anywhere in the repo** — not in compose files, Caddyfiles, scripts, docs,
  or examples. Secrets are named (env var, SSM parameter path) and described, never valued.
  `.env` and `*.mmdb` are gitignored and dockerignored; check `git status` before every commit.
- Dev compose (`infra/docker-compose.yml`) publishes ports on `127.0.0.1` only. Production compose
  (`infra/deploy/prod/docker-compose.yml`) never host-publishes `api`, `worker`, `postgres` or
  `redis` — only Caddy's 80/443 are exposed.
- **Migrations never run at container start.** The documented command is
  `docker compose … run --rm api uv run alembic upgrade head`; the api image's `CMD` is uvicorn
  and nothing else. The `worker` service shares the api image with a different `command:`.
- Images are built from the repo root context (`build: {context: .., dockerfile: infra/…}`),
  run as a non-root user, and carry a stdlib-only `HEALTHCHECK`. Production tags are git SHAs,
  never a moving `latest`.
- `infra/deploy/prod/*` are **synced copies of what runs on the box**. Change procedure: edit here
  → review → apply via SSM → re-run `infra/deploy/VERIFY.md` → commit any drift back in the same
  sitting. Docs must equal reality.
- Forwarded-IP trust (`FORWARDED_ALLOW_IPS=*`) is only valid while Caddy overwrites
  `X-Forwarded-For` **and** the api container is not host-published. Touching either requires
  re-running the spoof check in `VERIFY.md` before the change is considered done.
- Every production service sets json-file log rotation (`max-size 10m`, `max-file 3`). Backups
  (`pg_dump` → S3) run nightly; the restore procedure is written down and rehearsed once.
- The honeypot host shares **nothing** with the app host except `INGEST_HMAC_SECRET`: separate
  VPC/account, SSM-only management, no `sshd`, egress limited to 443. Assume it is compromised.
  Its shipper spools locally and never blocks Cowrie.
- `honeypot/assets.yaml` describes the fleet for `get_asset_info`; it holds roles and
  criticality, never hostnames' real credentials or internal addresses beyond what the honeypot
  already exposes.
- Never commit a raw Cowrie log or `honeypot/data/`.
- `docker compose ... config` interpolates `.env` into its output. Always redirect it (`> /dev/null`, or `--format json` piped only into a parser that never echoes); never paste its output into a report, ledger, or transcript. (M2 task-05: an agent's first `config` call printed real secrets into its transcript; the local dev secrets were rotated.)
