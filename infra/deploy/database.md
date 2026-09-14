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

The dump path is named once, and every step is chained with `&&` so that a failure anywhere stops
the sequence instead of running the next, more destructive, command anyway (task-04 review N4 —
these lines are pasted into a root shell under stress, where a silently-failed first line and a
successful `DROP DATABASE` is the worst outcome available):

```sh
DUMP=/var/backups/sentinelbrief/<the chosen dump>.sql.gz
gunzip -t "$DUMP" \
  && [ "$(gunzip -c "$DUMP" | wc -c)" -gt 0 ] \
  && docker compose -f /opt/sentinelbrief/docker-compose.yml stop api worker \
  && docker compose -f /opt/sentinelbrief/docker-compose.yml exec -T postgres psql -U sentinel -d postgres -c 'DROP DATABASE sentinelbrief WITH (FORCE)' -c 'CREATE DATABASE sentinelbrief' \
  && gunzip -c "$DUMP" | docker compose -f /opt/sentinelbrief/docker-compose.yml exec -T postgres psql -U sentinel -d sentinelbrief -v ON_ERROR_STOP=1 \
  && docker compose -f /opt/sentinelbrief/docker-compose.yml start api worker
```

If the restore step fails, the chain stops before the final `start` and `api`/`worker` stay down —
deliberately. Do not start them by hand against a half-restored database; fix the dump (or pick an
older one) and run the whole chain again.

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
`postgres/` from S3 (the instance role's `s3:GetObject` on `postgres/*` —
`infra/deploy/iam/app-host-inline.json`'s `BackupBucketReadOwnDumps` statement, task-05 fix-1),
creates `sentinelbrief_restore_check` fresh (`DROP DATABASE IF EXISTS` +
`CREATE DATABASE`), restores the dump into it with `-v ON_ERROR_STOP=1` (a partial restore fails
loudly instead of silently succeeding), prints one `restore ok alerts=… verdicts=…
tool_calls=… alembic=…` line from four `SELECT`s (`count(*)` on `alerts`, `verdicts`,
`tool_calls`, and `version_num` from `alembic_version`), then drops the scratch database and
removes the local copy. Task-05's walkthrough ran it once against the deployed host:

```
restore ok alerts=0 verdicts=0 tool_calls=0 alembic=0001
```

_(2026-09-12, walkthrough step 11, run before any honeypot session existed yet — hence the zero
row counts; the same line is pasted into [`VERIFY.md`](VERIFY.md) check 7.)_

## Retention decision (owner, recorded before the 48 h soak)

M6 keeps everything: no pruning of `alerts.raw` or verdicts. The soak measures growth (bytes per
session × sessions per day from `VERIFY.md` check 7's `pg_database_size` line); the numbers set
the M8 decision (candidate policy: prune `alerts.raw` events older than 90 days, keep envelope +
verdicts forever). A pruning job is out of M6 scope (`SUGGESTIONS.md`).

Measured at the 48 h soak (`.superpowers/sdd/m6-real-data-deploy/progress.md`, "Soak T+24 h" /
"Soak T+48 h" lines):

| Metric | Value |
|---|---|
| Volume | ≈ 110–120 alerts/day (24 in the first 5.2 h on 09-12, 112 on 09-13, 148 on 09-14 to 18:39Z; 284 total from 125 distinct source IPs at T+48 h) |
| Storage per alert | ≈ 5.5 KiB across the three tables (`alerts`, `verdicts`, `tool_calls`) including indexes (655,360 B / 116 rows at T+24 h) |
| Growth rate | ≈ 0.6 MB/day |
| Projected 90-day size | ≈ 55 MB |
| Projected 365-day size | ≈ 220 MB (against a 20 GB root volume, 17% used at T+48 h) |
| `pg_database_size` | 8,351 kB at T+24 h → 8,919 kB at T+48 h |
| Raw payload proxy | ≈ 1.8 KB/session (`raw_bytes_mean`, `check_real_sessions.py`) |

**Decision:** keep everything stands — no pruning job in v1 (the projected 365-day size is a
rounding error against a 20 GB volume).

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
