#!/usr/bin/env bash
# infra/deploy/prod/fetch-secrets.sh — fetch SentinelBrief's secrets from SSM Parameter Store and
# render them into two root-only files under /opt/sentinelbrief/ (PRD §11, §10.5, m6 task-03).
# Runs ON THE BOX as root, using the instance role, which can decrypt the SecureString params.
# Prints only counts — never a value.
#
# OUT (/opt/sentinelbrief/.env): the api/worker secrets — DATABASE_URL, LLM_API_KEY,
# INGEST_HMAC_SECRET, ADMIN_TOKEN (required) plus ABUSEIPDB_API_KEY (optional: an absent
# parameter is written as an empty value, and this script prints one "optional parameter absent"
# line naming it — never silently).
#
# OUT_PG (/opt/sentinelbrief/.env.postgres): POSTGRES_PASSWORD only — the same password
# DATABASE_URL embeds (env-checklist.md). Kept in its own file so postgres never loads the app's
# other secrets via its env_file.
#
# The MaxMind geoip license key is NOT fetched or rendered here — it is read inline by the
# deploy-time geoip one-off (prod/README.md) and never lands in a file on disk.
#
# Atomic replace (m6 task-03 fix-1, I1/M4; both-or-neither at the M6 final review, review N1):
# `$OUT`/`$OUT_PG` below are the `.tmp` working paths — every fetch is built up there (umask 077,
# then an explicit `chmod 600`) and both are `mv`'d onto their real `$OUT_DEST`/`$OUT_PG_DEST`
# paths together, at the end, only after every REQUIRED parameter for BOTH files resolved. A
# transient SSM/network failure on a required parameter therefore leaves the EXISTING
# `.env`/`.env.postgres` untouched and this script exits non-zero — never an empty file the next
# `docker compose up -d` would boot against (which `restart: unless-stopped` would turn into a
# crash-loop). The trap below removes both `.tmp` files on any exit, success or failure, so a
# failed run leaves no partial file behind either.
set -euo pipefail

REGION=us-east-1
OUT_DEST=/opt/sentinelbrief/.env
OUT_PG_DEST=/opt/sentinelbrief/.env.postgres
OUT="$OUT_DEST.tmp"
OUT_PG="$OUT_PG_DEST.tmp"

trap 'rm -f "$OUT" "$OUT_PG"' EXIT

umask 077

fetch_param() {
  aws ssm get-parameter --region "$REGION" --name "/sentinelbrief/$1" --with-decryption \
    --query 'Parameter.Value' --output text
}

: > "$OUT"
chmod 600 "$OUT"
required_count=0
for P in DATABASE_URL LLM_API_KEY INGEST_HMAC_SECRET ADMIN_TOKEN; do
  V="$(fetch_param "$P")"
  printf '%s=%s\n' "$P" "$V" >> "$OUT"
  required_count=$((required_count + 1))
done

optional_fetched=0
optional_empty=0
for P in ABUSEIPDB_API_KEY; do
  if V="$(fetch_param "$P" 2>/dev/null)"; then
    printf '%s=%s\n' "$P" "$V" >> "$OUT"
    optional_fetched=$((optional_fetched + 1))
  else
    printf '%s=\n' "$P" >> "$OUT"
    optional_empty=$((optional_empty + 1))
    echo "optional parameter absent: $P (written empty)"
  fi
done

: > "$OUT_PG"
chmod 600 "$OUT_PG"
for P in POSTGRES_PASSWORD; do
  V="$(fetch_param "$P")"
  printf '%s=%s\n' "$P" "$V" >> "$OUT_PG"
done

# Both `mv`s happen only after EVERY required fetch above succeeded (task-03 review N1): the two
# files carry the same password (DATABASE_URL embeds POSTGRES_PASSWORD), so flipping the first one
# before the second fetch could leave a half-rendered PAIR — a new app secret set against an old
# postgres password — which is worse than leaving both old.
mv "$OUT" "$OUT_DEST"
mv "$OUT_PG" "$OUT_PG_DEST"

echo "OK wrote $(wc -l < "$OUT_DEST") vars to $OUT_DEST ($required_count fetched required, $optional_fetched fetched optional, $optional_empty written empty) and 1 var to $OUT_PG_DEST"
