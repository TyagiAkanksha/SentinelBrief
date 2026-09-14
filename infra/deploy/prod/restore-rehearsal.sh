#!/usr/bin/env bash
# infra/deploy/prod/restore-rehearsal.sh → /opt/sentinelbrief/restore-rehearsal.sh (root, 0700).
# Usage: restore-rehearsal.sh [s3-key]  (default: the newest object under postgres/)
#
# Restores the latest (or a named) backup into a disposable scratch database, counts rows, and
# drops it. NEVER touches the live `sentinelbrief` database — only `sentinelbrief_restore_check`
# and `postgres` (the maintenance database used to create/drop it) are ever passed as `-d`.
# docs/deployment.md "Data"; infra/deploy/database.md "Restore".
set -euo pipefail
# shellcheck source=backup.env
source /opt/sentinelbrief/backup.env
: "${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET must be set in /opt/sentinelbrief/backup.env}"
COMPOSE=(docker compose -f /opt/sentinelbrief/docker-compose.yml)
LOCAL_DIR=/var/backups/sentinelbrief
LOCAL_COPY="$LOCAL_DIR/rehearsal.sql.gz"

KEY="${1:-}"
if [ -z "$KEY" ]; then
  KEY="$(aws s3api list-objects-v2 --bucket "$BACKUP_S3_BUCKET" --prefix postgres/ \
    --query 'sort_by(Contents, &LastModified)[-1].Key' --output text)"
fi

mkdir -p "$LOCAL_DIR"

# 1.
aws s3 cp "s3://$BACKUP_S3_BUCKET/$KEY" "$LOCAL_COPY" --only-show-errors

# 2.
"${COMPOSE[@]}" exec -T postgres psql -U sentinel -d postgres \
  -c 'DROP DATABASE IF EXISTS sentinelbrief_restore_check' \
  -c 'CREATE DATABASE sentinelbrief_restore_check'

# 3.
gunzip -c "$LOCAL_COPY" | "${COMPOSE[@]}" exec -T postgres psql -U sentinel -d sentinelbrief_restore_check -v ON_ERROR_STOP=1 -q -o /dev/null

# 4.
ALERTS="$("${COMPOSE[@]}" exec -T postgres psql -U sentinel -d sentinelbrief_restore_check -tAc 'select count(*) from alerts')"
VERDICTS="$("${COMPOSE[@]}" exec -T postgres psql -U sentinel -d sentinelbrief_restore_check -tAc 'select count(*) from verdicts')"
TOOL_CALLS="$("${COMPOSE[@]}" exec -T postgres psql -U sentinel -d sentinelbrief_restore_check -tAc 'select count(*) from tool_calls')"
ALEMBIC="$("${COMPOSE[@]}" exec -T postgres psql -U sentinel -d sentinelbrief_restore_check -tAc 'select version_num from alembic_version')"
echo "restore ok alerts=${ALERTS// /} verdicts=${VERDICTS// /} tool_calls=${TOOL_CALLS// /} alembic=${ALEMBIC// /}"

# 5.
"${COMPOSE[@]}" exec -T postgres psql -U sentinel -d postgres -c 'DROP DATABASE sentinelbrief_restore_check'
rm -f "$LOCAL_COPY"
