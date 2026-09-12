# Database: where the data lives, backups, restore, retention

This is the doc of record for SentinelBrief's Postgres data on the production single-host
topology (`docs/deployment.md` "Data: backups and retention" points here). Written before any
real attacker data lands — the restore procedure and the retention decision below are both
recorded before the M6 48 h soak starts.

## Where the data lives

Postgres data lives on the named Docker volume `sentinelbrief_pg` (both compose files pin
`name: sentinelbrief` at the top level, so Compose's actual on-disk volume name is
`sentinelbrief_sentinelbrief_pg` — project name + volume key; verified against the running dev
stack below), on the app host's root EBS volume
(`infra/deploy/prod/docker-compose.yml`'s `postgres` service). Inspect it on the box:

```sh
docker volume inspect sentinelbrief_sentinelbrief_pg
```

## Backups

`/opt/sentinelbrief/backup.sh` (committed copy: `infra/deploy/prod/backup.sh`) runs nightly from
`sentinelbrief-backup.timer` (03:15 UTC, `Persistent=true` so a missed run catches up at the next
boot). It dumps the live `sentinelbrief` database with `pg_dump -U sentinel -d sentinelbrief
--format=plain --no-owner --no-privileges` through the running compose `postgres` service, gzips
the output, and uploads it to `s3://$BACKUP_S3_BUCKET/postgres/sentinelbrief-<UTC stamp>.sql.gz`
(key layout: `postgres/sentinelbrief-<UTC stamp>.sql.gz`, e.g.
`postgres/sentinelbrief-20260911T031500Z.sql.gz`) using the instance role's `s3:PutObject`
permission — no AWS credentials are stored on disk. The script keeps the 3 newest dumps under
`/var/backups/sentinelbrief/` locally and prunes older ones; the S3 copy is bounded by the
30-day lifecycle rule (`infra/deploy/s3-lifecycle.json`, applied once by the owner — see that
file's own comment for the `put-bucket-lifecycle-configuration` command).

Run one now (e.g. right before the soak, or to check the timer is wired correctly):

```sh
systemctl start sentinelbrief-backup.service && journalctl -u sentinelbrief-backup -n 5
```

## Restore

**Full restore into the live database** (disaster recovery — this destroys and recreates the
`sentinelbrief` database, so it is only ever run when the live data is already lost or known
bad). Verify the chosen dump is intact BEFORE anything touches the live database — a truncated or
empty dump must never be discovered only after the live database is already gone — then stop the
app, drop/recreate, restore with `ON_ERROR_STOP=1` so a partial restore aborts loudly instead of
silently succeeding, and only then start the app back up:

```sh
gunzip -t /var/backups/sentinelbrief/<the chosen dump>.sql.gz && [ "$(gunzip -c /var/backups/sentinelbrief/<the chosen dump>.sql.gz | wc -c)" -gt 0 ]
docker compose -f /opt/sentinelbrief/docker-compose.yml stop api worker
docker compose -f /opt/sentinelbrief/docker-compose.yml exec -T postgres psql -U sentinel -d postgres -c 'DROP DATABASE sentinelbrief WITH (FORCE)' -c 'CREATE DATABASE sentinelbrief'
gunzip -c /var/backups/sentinelbrief/<the chosen dump>.sql.gz | docker compose -f /opt/sentinelbrief/docker-compose.yml exec -T postgres psql -U sentinel -d sentinelbrief -v ON_ERROR_STOP=1
docker compose -f /opt/sentinelbrief/docker-compose.yml start api worker
```

`WITH (FORCE)` (Postgres 13+; the box runs `postgres:16`) disconnects the `postgres` container's
own `pg_isready -U sentinel -d sentinelbrief` healthcheck (`infra/deploy/prod/docker-compose.yml`,
every 5 s) so `DROP DATABASE` cannot fail with "database is being accessed by other users" even
though `api`/`worker` are already stopped.

Then run `VERIFY.md` check 0 against the restored data. **Warning:** `redis` may still hold ARQ
jobs enqueued for alerts that no longer exist in the restored database (any alert ingested after
the dump was taken) — the triage job (M5 task-02) treats a missing alert as `missing` and exits
cleanly rather than erroring, so this is expected and not itself a sign of a bad restore.

**Rehearsal** (does *not* touch the live database — restores into a disposable scratch database,
counts rows, and drops it): `/opt/sentinelbrief/restore-rehearsal.sh` (committed copy:
`infra/deploy/prod/restore-rehearsal.sh`). It copies the newest (or a named) object under
`postgres/` from S3, creates `sentinelbrief_restore_check` fresh (`DROP DATABASE IF EXISTS` +
`CREATE DATABASE`), restores the dump into it with `-v ON_ERROR_STOP=1` (a partial restore fails
loudly instead of silently succeeding), prints `restore ok alerts=<n> verdicts=<n>
tool_calls=<n> alembic=<version>` from four `SELECT`s (`count(*)` on `alerts`, `verdicts`,
`tool_calls`, and `version_num` from `alembic_version`), then drops the scratch database and
removes the local copy. Task-05's walkthrough runs this once against the deployed host; task-06
pastes its output here:

```
restore ok alerts=<n> verdicts=<n> tool_calls=<n> alembic=<version>
```

(recorded during deployment)

## Retention decision (owner, recorded before the 48 h soak)

M6 keeps everything: no pruning of `alerts.raw` or verdicts. The soak measures growth (bytes per
session × sessions per day from `VERIFY.md` check 7's `pg_database_size` line); the numbers set
the M8 decision (candidate policy: prune `alerts.raw` events older than 90 days, keep envelope +
verdicts forever). A pruning job is out of M6 scope (`SUGGESTIONS.md`).

Measured at soak: `<rows/day>` `<MB/day>` `<projected 90-day size>`

## Logs (footnote — task-05 copies these into `VERIFY.md` check 8 verbatim)

**App host** — every container's logging driver:

```sh
docker inspect --format '{{.HostConfig.LogConfig}}' $(docker compose -f /opt/sentinelbrief/docker-compose.yml ps -q)
```

Every line should read `{json-file map[max-file:3 max-size:10m]}`. Rotated files appear once a
container has logged more than 10 MB (expect this on `api` under attacker traffic):

```sh
sudo ls -la /var/lib/docker/containers/*/*-json.log*
```

**Honeypot host** — the same `docker inspect` check against the `cowrie` container, plus the
journald budget that bounds Cowrie's chatty output:

```sh
journalctl --disk-usage
```

`/etc/systemd/journald.conf`'s `SystemMaxUse=` — set to `200M` by task-01's user-data
(`honeypot/README.md` step 3).
