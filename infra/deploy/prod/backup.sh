#!/usr/bin/env bash
# infra/deploy/prod/backup.sh  → /opt/sentinelbrief/backup.sh (root:root 0700)
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
