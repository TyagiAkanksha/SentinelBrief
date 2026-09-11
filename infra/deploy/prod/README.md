# Production config — synced copies (doc of record)

**Source of truth is the box**, `/opt/sentinelbrief/` on the deployed EC2 instance — not this
directory. The files here (`docker-compose.yml`, `Caddyfile`, `fetch-secrets.sh`) are committed,
byte-identical copies of what actually runs, so the production configuration is reviewable,
diffable, and greppable from the repo instead of asserted-only (`.claude/rules/infra.md`), and so
disaster recovery has something concrete to restore from. They are not applied from here
automatically — nothing reads this directory at deploy time.

## Change procedure

1. Edit the file **here**, in the repo, and get the change reviewed like any other commit.
2. Apply it to the box via SSM (`infra/deploy/ec2-single-host.md` has the session command) — copy
   the file to `/opt/sentinelbrief/` and re-run/restart whatever the change requires
   (`docker compose up -d` for compose changes, `docker compose restart caddy` for `Caddyfile`
   changes).
3. Re-run the relevant checks in `infra/deploy/VERIFY.md` against the live deployment.
4. If applying the change on the box surfaced any drift from what's committed here (a manual fix
   made directly on the box, a value that had to differ), commit that drift back in the same
   sitting — docs must equal reality.

`fetch-secrets.sh` runs **on the box only** (it uses the instance's IAM role to decrypt SSM
`SecureString` parameters); it is not meant to be run from a workstation.

## Migrations

For a release that ships a new Alembic migration: bumping the `api`/`worker` image tag in
`docker-compose.yml` (see "Image tags" below) is what selects the new image; `docker compose up -d`
only recreates the container from whatever tag is already in the file. Running
`docker compose exec api …` therefore runs INSIDE the still-running OLD container — its image has
no new migration yet, so `alembic upgrade head` there resolves to the schema already applied and
silently no-ops. Run the migration from the NEW image instead, in a one-off container that
doesn't touch the running service, **before** `docker compose up -d`:

```sh
docker compose pull api && docker compose run --rm api uv run alembic upgrade head
```

Never `docker compose exec api …` for a migration — see above. A **fresh** deploy or upgrade to
this release must run the same `pull`/`run --rm` command — there is no running `api` container to
`exec` into yet, so this form covers both cases.

## Image tags

The `api`/`worker`/`web` image tags in `docker-compose.yml` are pinned to the deployed git SHA,
not a moving tag like `latest`. Each redeploy pushes new images tagged with the new SHA
(`../push_ecr.sh`) and updates the tag in this file (both the box's copy and this committed copy)
to match — bump **both** `web` and `api`/`worker` to the same SHA in both copies, so at any point
in time this file names exactly what's running.

## Geoip one-off

The GeoLite2 `.mmdb` files are fetched deploy-time, never baked into the image or run
automatically at container start. The MaxMind license key is read from SSM inline and passed only
to a throwaway container — it never lands in a file on disk (the services keep their bind mount
`:ro`; the rw override below is for this one-off only):

```sh
MAXMIND_LICENSE_KEY="$(aws ssm get-parameter --region us-east-1 --name \
  /sentinelbrief/MAXMIND_LICENSE_KEY --with-decryption --query Parameter.Value --output text)" \
  docker compose run --rm -e MAXMIND_LICENSE_KEY -v /opt/sentinelbrief/geoip:/app/infra/geoip api \
  uv run python scripts/fetch_geoip.py --out-dir infra/geoip
```

## Secrets rotation

1. Update the parameter value in SSM.
2. `./fetch-secrets.sh` (re-renders `/opt/sentinelbrief/.env` and `/opt/sentinelbrief/.env.postgres`).
3. `docker compose up -d api worker` (and `postgres` only if `POSTGRES_PASSWORD` changed — which
   also needs `ALTER USER sentinel WITH PASSWORD '...'` run inside the database, since Postgres
   does not re-read its own env on a container restart).
