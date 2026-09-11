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
set -euo pipefail

REGION=us-east-1
OUT=/opt/sentinelbrief/.env
OUT_PG=/opt/sentinelbrief/.env.postgres

umask 077

fetch_param() {
  aws ssm get-parameter --region "$REGION" --name "/sentinelbrief/$1" --with-decryption \
    --query 'Parameter.Value' --output text
}

: > "$OUT"
required_count=0
for P in DATABASE_URL LLM_API_KEY INGEST_HMAC_SECRET ADMIN_TOKEN; do
  V="$(fetch_param "$P")"
  printf '%s=%s\n' "$P" "$V" >> "$OUT"
  required_count=$((required_count + 1))
done

optional_count=0
for P in ABUSEIPDB_API_KEY; do
  if V="$(fetch_param "$P" 2>/dev/null)"; then
    printf '%s=%s\n' "$P" "$V" >> "$OUT"
    optional_count=$((optional_count + 1))
  else
    printf '%s=\n' "$P" >> "$OUT"
    echo "optional parameter absent: $P (written empty)"
  fi
done
chmod 600 "$OUT"

: > "$OUT_PG"
for P in POSTGRES_PASSWORD; do
  V="$(fetch_param "$P")"
  printf '%s=%s\n' "$P" "$V" >> "$OUT_PG"
done
chmod 600 "$OUT_PG"

echo "OK wrote $((required_count + optional_count)) vars to $OUT and 1 var to $OUT_PG"
