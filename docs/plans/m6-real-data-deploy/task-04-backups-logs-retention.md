---
id: task-04
milestone: m6-real-data-deploy
depends_on: [task-03]
status: planned
spec: PRD.md §11 Phase 1 ("Data: Postgres on a named volume; nightly `pg_dump | gzip` to an S3 bucket with a 30-day lifecycle …; log rotation on every service from day one — honeypot traffic is chatty"), §12 M6 accept clause "backups and log rotation observed working"; `docs/deployment.md` → "Data: backups and retention" (the restore procedure is documented in `infra/deploy/database.md` and rehearsed once; the `alerts.raw` retention policy is an owner decision recorded there BEFORE the 48 h soak) and "Logs"; `docs/plans/m6-real-data-deploy.md` Global Constraints ("Every production service has json-file log rotation; the backup job runs before the 48 h soak starts and a restore is rehearsed once"); `.claude/rules/infra.md` ("Backups (`pg_dump` → S3) run nightly; the restore procedure is written down and rehearsed once")
---

# task-04 — Backups: nightly `pg_dump | gzip` → S3 from a host systemd timer, the S3 lifecycle rule, the restore procedure + rehearsal script, the retention decision; log rotation written down for both hosts

## Goal

A backup that exists before the first real byte of attacker data does: a root-owned script on the
app host dumps the compose Postgres, gzips it, uploads it to a private S3 bucket under a dated key
and keeps the three newest copies locally; a systemd timer runs it nightly (persistent, so a missed
run catches up at boot); a 30-day lifecycle rule bounds the bucket; and `infra/deploy/database.md`
holds the restore procedure as exact commands plus a rehearsal recipe that restores the latest
dump into a scratch database, counts rows, and drops it — task-05's walkthrough runs the rehearsal
once and task-06 pastes its output. The same document records the `alerts.raw` retention decision
the doc of record demands before the soak. Log rotation needs no new mechanism (both compose files
already set the json-file driver on every service); this task writes the observation commands for
both hosts into `VERIFY.md`'s stub (task-05 finishes that file) and adds the shipper's journald
line. **Ruling (briefing):** PRD §11 says "from a cron container"; this task uses a host systemd
timer running as root with the instance role instead — no extra image, no AWS credentials inside a
container, and `pg_dump` runs through `docker compose exec` against the running service. PRD §11
is amended (v1.5) to say so. Cost if wrong: the timer/service pair is replaced by one compose
service later.

## Context (read ONLY these)

- `PRD.md` §11 (Data bullet), §12 M6.
- `docs/deployment.md` → "Data: backups and retention", "Logs".
- `docs/plans/m6-real-data-deploy.md` — Global Constraints.
- `.claude/rules/infra.md`.
- Task-03's outputs (read, do not modify except where listed): `infra/deploy/prod/docker-compose.yml`
  (service name `postgres`, user `sentinel`, db `sentinelbrief`; the file lives at
  `/opt/sentinelbrief/docker-compose.yml` on the box), `infra/deploy/prod/README.md` (you add a
  "Backups" section), `infra/deploy/env-checklist.md` (you add `BACKUP_S3_BUCKET`).
- `tests/test_deploy_scripts.py` (task-03; the `bash -n` + text-pin pattern — copy, do not import).

## Files

- Create: `infra/deploy/prod/backup.sh` (executable), `infra/deploy/prod/backup.env`,
  `infra/deploy/prod/sentinelbrief-backup.service`, `infra/deploy/prod/sentinelbrief-backup.timer`,
  `infra/deploy/prod/restore-rehearsal.sh` (executable), `infra/deploy/s3-lifecycle.json`,
  `infra/deploy/database.md`
- Create (test-author): `tests/test_backup_artifacts.py`
- Modify: `docs/deployment.md` ("Data" section: timer not container; the three file names; the
  rehearsal; AND the "On the box" sentence at line 64, "the backup cron container" → "the backup
  timer's script"), `PRD.md` §11 Data bullet + changelog v1.5 (one line: "nightly `pg_dump | gzip` to
  S3 from a host systemd timer using the instance role (was: a cron container)"),
  `infra/deploy/prod/README.md` ("Backups" section), `infra/deploy/env-checklist.md`
  (`BACKUP_S3_BUCKET` row under a "Host (root) — backup timer" table), `.claude/rules/infra.md`
  (the backups bullet names the timer and `database.md`)

## Interfaces

- **Consumes:** task-03's compose service names and the box paths.
- **Produces (task-05 installs, task-06 observes — produce exactly):**

  ```bash
  # infra/deploy/prod/backup.sh  → /opt/sentinelbrief/backup.sh (root:root 0700)
  #!/usr/bin/env bash
  # Nightly Postgres backup: pg_dump | gzip → /var/backups/sentinelbrief/ → s3://$BACKUP_S3_BUCKET/postgres/.
  # Runs as root from sentinelbrief-backup.timer using the instance role (s3:PutObject on the bucket).
  # Prints key names and byte counts only — never data. docs/deployment.md "Data"; infra/deploy/database.md.
  set -euo pipefail
  # shellcheck source=backup.env
  source /opt/sentinelbrief/backup.env                     # BACKUP_S3_BUCKET=<bucket> (non-secret, instance-specific)
  : "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET must be set in /opt/sentinelbrief/backup.env}"
  COMPOSE=(docker compose -f /opt/sentinelbrief/docker-compose.yml)
  LOCAL_DIR=/var/backups/sentinelbrief
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  OUT="$LOCAL_DIR/sentinelbrief-$STAMP.sql.gz"
  mkdir -p "$LOCAL_DIR"; chmod 700 "$LOCAL_DIR"
  "${COMPOSE[@]}" exec -T postgres pg_dump -U sentinel -d sentinelbrief --format=plain --no-owner --no-privileges | gzip -6 > "$OUT"
  test -s "$OUT"                                           # an empty dump is a failure, not a backup
  aws s3 cp "$OUT" "s3://$BACKUP_S3_BUCKET/postgres/$(basename "$OUT")" --only-show-errors
  echo "backup ok key=postgres/$(basename "$OUT") bytes=$(stat -c %s "$OUT")"
  ls -1t "$LOCAL_DIR"/sentinelbrief-*.sql.gz | tail -n +4 | xargs -r rm -f    # keep the 3 newest locally
  ```

  ```ini
  # infra/deploy/prod/backup.env → /opt/sentinelbrief/backup.env (root:root 0600; committed copy carries the REAL bucket name after task-05 — it is not a secret)
  BACKUP_S3_BUCKET=sentinelbrief-backups-181040156847        # the real bucket name (account id verified 2026-09-11; not a secret) — the bucket itself is created at task-05 step 2

  # infra/deploy/prod/sentinelbrief-backup.service → /etc/systemd/system/
  [Unit]
  Description=SentinelBrief nightly Postgres backup to S3
  After=docker.service
  Requires=docker.service
  [Service]
  Type=oneshot
  ExecStart=/opt/sentinelbrief/backup.sh

  # infra/deploy/prod/sentinelbrief-backup.timer → /etc/systemd/system/  (systemctl enable --now sentinelbrief-backup.timer)
  [Unit]
  Description=Run sentinelbrief-backup nightly
  [Timer]
  OnCalendar=*-*-* 03:15:00 UTC
  Persistent=true
  RandomizedDelaySec=600
  [Install]
  WantedBy=timers.target
  ```

  ```json
  // infra/deploy/s3-lifecycle.json — applied once by the owner:
  //   aws s3api put-bucket-lifecycle-configuration --bucket sentinelbrief-backups-<id> --lifecycle-configuration file://infra/deploy/s3-lifecycle.json
  {"Rules": [{"ID": "expire-postgres-dumps-30d", "Status": "Enabled", "Filter": {"Prefix": "postgres/"}, "Expiration": {"Days": 30}, "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2}}]}
  ```

  ```bash
  # infra/deploy/prod/restore-rehearsal.sh → /opt/sentinelbrief/restore-rehearsal.sh (root, 0700). Usage: restore-rehearsal.sh [s3-key]  (default: the newest object under postgres/)
  # 1. aws s3 cp s3://$BACKUP_S3_BUCKET/<key> /var/backups/sentinelbrief/rehearsal.sql.gz
  # 2. docker compose exec -T postgres psql -U sentinel -d postgres -c 'DROP DATABASE IF EXISTS sentinelbrief_restore_check' -c 'CREATE DATABASE sentinelbrief_restore_check'
  # 3. gunzip -c … | docker compose exec -T postgres psql -U sentinel -d sentinelbrief_restore_check -v ON_ERROR_STOP=1 -q
  # 4. prints "restore ok alerts=<n> verdicts=<n> tool_calls=<n> alembic=<version>" from four SELECTs (count(*) ×3, version_num from alembic_version)
  # 5. DROP DATABASE sentinelbrief_restore_check; rm the local copy.  Never touches the live `sentinelbrief` database (pinned: the string "-d sentinelbrief " with a trailing space appears nowhere; only "-d sentinelbrief_restore_check" and "-d postgres").
  ```

  `infra/deploy/database.md` — sections: **Where the data lives** (named volume `sentinelbrief_pg`
  on the root EBS volume; `docker volume inspect` command); **Backups** (what `backup.sh` does,
  the key layout `postgres/sentinelbrief-<UTC stamp>.sql.gz`, the local retention of 3, the S3
  lifecycle of 30 days, how to run one now: `systemctl start sentinelbrief-backup.service &&
  journalctl -u sentinelbrief-backup -n 5`); **Restore** (full restore into the live database —
  the exact sequence: `docker compose stop api worker` → `psql -d postgres -c 'DROP DATABASE
  sentinelbrief' -c 'CREATE DATABASE sentinelbrief'` → `gunzip -c … | psql -d sentinelbrief` →
  `docker compose start api worker` → `VERIFY.md` check 0 — with the warning that `redis` may hold
  jobs for alerts that no longer exist, which the job treats as `missing` (M5 task-02); and the
  rehearsal via `restore-rehearsal.sh` with a `(recorded during deployment)` block); **Retention
  decision (owner, recorded before the 48 h soak)** — the decision text: "M6 keeps everything:
  no pruning of `alerts.raw` or verdicts. The soak measures growth (bytes per session × sessions
  per day from `VERIFY.md` check 7's `pg_database_size` line); the numbers set the M8 decision
  (candidate policy: prune `alerts.raw` events older than 90 days, keep envelope + verdicts
  forever). A pruning job is out of M6 scope (`SUGGESTIONS.md`)." — and a one-line table `Measured
  at soak: <rows/day> <MB/day> <projected 90-day size>` filled by task-06.

  **Log rotation (both hosts) — observation commands** written into `infra/deploy/VERIFY.md`'s
  check 8 by task-05 (this task provides them in `database.md`'s "Logs" footnote so task-05 copies
  verbatim): app host `docker inspect --format '{{.HostConfig.LogConfig}}' $(docker compose -f
  /opt/sentinelbrief/docker-compose.yml ps -q)` → every line `{json-file map[max-file:3
  max-size:10m]}`; `sudo ls -la /var/lib/docker/containers/*/*-json.log*` (rotated `.1`/`.2`
  files appear once a container has logged > 10 MB — the api under attacker traffic); honeypot
  host: the same `docker inspect` for the cowrie container, plus `journalctl --disk-usage` and
  `/etc/systemd/journald.conf`'s `SystemMaxUse=` (set to `200M` by task-01's user-data —
  **amend task-01's runbook step 3** with that one line; the implementer of THIS task edits
  `honeypot/README.md` accordingly and says so in the report).

## Interfaces → test table

`tests/test_backup_artifacts.py` — pure text/JSON pins, no docker.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| backup.sh | `::test_backup_script_shape` | `bash -n` exits 0; executable bit; contains `set -euo pipefail`, `pg_dump -U sentinel -d sentinelbrief`, `--format=plain`, `gzip`, `aws s3 cp`, `test -s`, `tail -n +4`; does NOT contain `pg_dumpall`, `--password`, `PGPASSWORD=`; the only `echo` line prints `backup ok key=` (no `$OUT` content) |
| backup.env | `::test_backup_env_has_only_the_bucket` | exactly one uncommented assignment, `BACKUP_S3_BUCKET=sentinelbrief-backups-` prefix |
| unit files | `::test_timer_and_service_shape` | timer: `OnCalendar=*-*-* 03:15:00 UTC`, `Persistent=true`, `WantedBy=timers.target`; service: `Type=oneshot`, `ExecStart=/opt/sentinelbrief/backup.sh`, `Requires=docker.service` |
| lifecycle | `::test_s3_lifecycle_expires_dumps_after_30_days` | `json.loads`; `Rules[0].Status == "Enabled"`, `.Filter.Prefix == "postgres/"`, `.Expiration.Days == 30` |
| rehearsal | `::test_restore_rehearsal_never_touches_the_live_db` | `bash -n`; contains `sentinelbrief_restore_check`, `ON_ERROR_STOP=1`, `DROP DATABASE IF EXISTS sentinelbrief_restore_check`, `alembic_version`; the regex `-d sentinelbrief(\s\|$)` (the live db name as a target) has 0 matches; `DROP DATABASE sentinelbrief;` absent |
| database.md | `::test_database_doc_sections` | headings "Backups", "Restore", "Retention decision" present; contains `restore-rehearsal.sh`, `30`-day sentence, `sentinelbrief_pg`, and — amended at the M6 final review (ruling R16) — a FILLED `restore ok alerts=<int> verdicts=<int> tool_calls=<int> alembic=<rev>` line with no "(recorded during deployment)" marker and no `alerts=<n>` placeholder block left (was: the marker present) |
| PRD + doc of record | `::test_prd_and_deployment_doc_say_systemd_timer` | `PRD.md` §11 contains "systemd timer"; `docs/deployment.md` "Data" section contains `sentinelbrief-backup.timer` and `database.md`; neither file still says "cron container" |
| checklist | extend `tests/test_env_checklist.py` (task-03's file, not pinned by THIS task's author — add `::test_backup_bucket_listed`) | `BACKUP_S3_BUCKET` row present, marked non-secret |

## Steps (TDD)

- [ ] **Step 1 (RED — test-author):** `tests/test_backup_artifacts.py` + the checklist extension.
- [ ] **Step 2 (RED — test-author):** `uv run pytest -q tests/test_backup_artifacts.py
  tests/test_env_checklist.py` → Expected: every new test fails on a missing file / missing text.
  Pin, commit `test(infra): backup script/timer/lifecycle/restore/retention pins RED (m6 task-04)`.
- [ ] **Step 3 (GREEN — implementer): the five prod files + `s3-lifecycle.json`** per Interfaces;
  `bash -n` both scripts; `shellcheck` if installed (paste; optional).
- [ ] **Step 4 (GREEN — implementer): rehearse locally.** Against the dev stack (M5 scratch
  override, five services healthy, seeded with `scripts/seed_dev.py`): run `backup.sh`'s pipeline
  by hand with `COMPOSE=(docker compose -f infra/docker-compose.yml)` and `aws s3 cp` replaced by
  `cp` into the scratchpad (a scratch copy of the script, never committed), then
  `restore-rehearsal.sh`'s steps 2–5 the same way → paste `restore ok alerts=<n> verdicts=<n>
  tool_calls=<n> alembic=<version>` and confirm the live dev DB's counts are unchanged. `down`.
- [ ] **Step 5 (implementer): docs** — `database.md`, `docs/deployment.md` Data section, PRD §11 +
  changelog v1.5 line, `prod/README.md` Backups section, `env-checklist.md` row,
  `.claude/rules/infra.md` bullet, `honeypot/README.md` journald line.
- [ ] **Step 6 (implementer): full gates (cold) → commit** `feat(infra): nightly pg_dump→S3 timer,
  lifecycle, restore rehearsal, retention decision (m6 task-04)`; path-scoped `git add
  infra/deploy docs/deployment.md PRD.md .claude/rules/infra.md honeypot/README.md`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_backup_artifacts.py tests/test_env_checklist.py tests/test_prod_compose.py   # all pass, 0 skipped (prod compose still has exactly six services)
bash -n infra/deploy/prod/backup.sh infra/deploy/prod/restore-rehearsal.sh && echo syntax-ok
grep -c "cron container" PRD.md docs/deployment.md                 # PRD.md:0 docs/deployment.md:0   (BASE: 1 and 1)
python3 -c "import json;d=json.load(open('infra/deploy/s3-lifecycle.json'));print(d['Rules'][0]['Expiration']['Days'])"   # 30
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- A committed backup script, timer, lifecycle rule and restore rehearsal exist, pass their pins,
  and were rehearsed once against the dev stack with the live database provably untouched.
- `infra/deploy/database.md` records the restore procedure and the retention decision before any
  real data lands; the PRD and the doc of record say "systemd timer", not "cron container".
- The log-rotation observation commands for both hosts are written down for task-05's `VERIFY.md`.
