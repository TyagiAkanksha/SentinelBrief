# VERIFY.md — deployed verification checklist (M6, PRD §12)

**This is a template.** Every check below is the exact command to run against the live, deployed
stack — run it for real during the deployment session ([`ec2-single-host.md`](ec2-single-host.md)
step 12) and paste the actual output into the matching ```text``` block in place of
`(recorded during deployment)`. No output here is fabricated.

Set these once, then reuse them in every command below:

```sh
API=https://api.sentinelbrief.tyagiakanksha.com
WEB=https://sentinelbrief.tyagiakanksha.com
```

## 0. Health check

```sh
curl -s $API/healthz
```

Expected: `{"status":"ok","db":"ok","redis":"ok"}`

```text
(recorded during deployment)
```

## 1. TLS + security headers

```sh
curl -sI $WEB | grep -iE 'strict-transport|x-frame|HTTP/'
curl -sI $API/healthz | grep -iE 'strict-transport|x-frame|HTTP/'
```

Expected: both print `HTTP/2 200`, a `Strict-Transport-Security` (HSTS) header, and
`X-Frame-Options: DENY`.

```text
(recorded during deployment)
```

## 2. Ingest gates (the body-cap semantics)

*(Rewritten at task-05 fix-1 — controller ruling R11/PC1, review I5/M2/M8: the deployed
`INGEST_MAX_BODY_BYTES` is `2,000,000`, `.env.example`'s default, not overridden by the prod
compose file — see `env-checklist.md`.)*

An unsigned POST is rejected before the body is even read:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/v1/alerts -H 'content-type: application/json' -d '{}'
```

Expected: `401`.

```text
(recorded during deployment)
```

A 1.9 MB unsigned body is UNDER the 2,000,000-byte cap, so the app's declared-length guard passes
it straight through to the HMAC signature check — **also `401`, not `413`** (the repo's own
`tests/test_ingest_body_cap.py::test_body_at_cap_reaches_signature_check` pins exactly this; a
`413` here would mean the cap regressed):

```sh
python3 -c "print('x'*1900000)" > /tmp/body-1.9mb.txt
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/v1/alerts -H 'content-type: application/json' --data-binary @/tmp/body-1.9mb.txt
```

Expected: `401`.

```text
(recorded during deployment)
```

A 2.1 MB body with a truthful `Content-Length` is OVER **both** caps — Caddy's own `request_body
{ max_size 2MB }` is also 2,000,000 bytes, so a recent Caddy version may answer on the declared
`Content-Length` before the app ever sees the request. Captured with `-i` (full response, headers
+ body) so the recorded envelope decides which layer actually answered — this is falsifiable, not
asserted (task-05 fix-2, review N4): there is no body size that isolates the app's cap from
Caddy's, since the two numbers are identical:

```sh
head -c 2100000 /dev/zero > /tmp/body-2.1mb.bin
curl -s -i -X POST $API/api/v1/alerts -H 'content-type: application/json' --data-binary @/tmp/body-2.1mb.bin
```

Expected: `HTTP/2 413` with a JSON body `{"error":{"code":"payload_too_large"...}}` (the app
answered first); if instead the body is not that JSON envelope, Caddy answered first — record
that finding instead of the expectation above.

```text
(recorded during deployment)
```

A SMALL chunked unsigned POST has no usable `Content-Length` — the app's guard fails closed:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/v1/alerts -H 'content-type: application/json' -H 'Transfer-Encoding: chunked' -d '{}'
```

Expected: `411 length_required`.

```text
(recorded during deployment)
```

A 3 MB CHUNKED body: no `Content-Length` is declared, so Caddy's own `request_body { max_size
2MB }` cuts the stream past 2,000,000 bytes before the app ever gets a full body to answer `411`
on. Captured with `-i` so the recorded output shows whichever layer actually answered — this is
falsifiable, not asserted: if the recorded body is the app's JSON envelope, record that finding
instead of the expectation below:

```sh
head -c 3000000 /dev/zero > /tmp/body-3mb.bin
curl -s -i -X POST $API/api/v1/alerts -H 'content-type: application/json' -H 'Transfer-Encoding: chunked' --data-binary @/tmp/body-3mb.bin
```

Expected: Caddy's own error status and page (not the app's `413` JSON envelope) — record whatever
Caddy actually returns.

```text
(recorded during deployment)
```

## 3. First real session `triaged`

Paste [`ec2-single-host.md`](ec2-single-host.md) step 10's three outputs here: the shipper's
`delivered ... status=202` line, the worker's `triage` log line, and the alert's
`"status": "triaged"` from `$API/api/v1/alerts`.

```text
(recorded during deployment)
```

## 4. CORS allow/deny

```sh
curl -sI -H 'Origin: https://evil.example.com' $API/healthz | grep -i access-control-allow-origin
curl -sI -H "Origin: $WEB" $API/healthz | grep -i access-control-allow-origin
```

Expected: the first prints nothing (no `access-control-allow-origin` header); the second echoes
`$WEB`.

```text
(recorded during deployment)
```

## 5. Dashboard

```sh
curl -s $WEB/alerts | grep -c '<table'
curl -s $WEB/healthz
```

Expected: the count is `>= 1`; the healthz body is `{"status":"ok"}`.

```text
(recorded during deployment)
```

## 6. No LLM call from any public request (PRD §12 M6, §10.1)

On the box:

```sh
docker compose logs api --since 24h 2>&1 | grep -ciE 'worker ready|triage job|triage attempt|triage failed|routing escalated|llm|openai'
```

Expected: `0` — the api process never logs a triage/LLM line.

```text
(recorded during deployment)
```

```sh
docker compose logs worker --since 24h 2>&1 | grep -ciE 'triage job|routing escalated|worker ready'
```

Expected: `> 0` — the worker is the only process doing triage work.

```text
(recorded during deployment)
```

Prove the api process never even *imports* the LLM client:

```sh
docker compose exec -T api uv run python -c "import sys; import api.main; print([m for m in sys.modules if m.startswith('worker') or m == 'core.llm' or m.startswith('openai')])"
```

Expected: `[]`.

```text
(recorded during deployment)
```

## 7. Backups

```sh
aws s3 ls s3://sentinelbrief-backups-181040156847/postgres/
journalctl -u sentinelbrief-backup -n 2
```

Expected: today's `sentinelbrief-<UTC stamp>.sql.gz` key; the journal's last line reads
`backup ok key=... bytes=...`.

```text
(recorded during deployment)
```

```sh
./restore-rehearsal.sh
```

Expected: `restore ok alerts=... verdicts=... tool_calls=... alembic=...` (paste the same line
into [`database.md`](database.md)'s own block).

```text
(recorded during deployment)
```

```sh
docker compose exec -T postgres psql -U sentinel -d sentinelbrief -tAc "select pg_size_pretty(pg_database_size('sentinelbrief'))"
```

Expected: a human-readable size (e.g. `12 MB`) — feeds `database.md`'s retention decision.

```text
(recorded during deployment)
```

## 8. Log rotation

App host — every service's logging driver, and the rotated files once any container has logged
more than 10 MB (`infra/deploy/database.md`'s "Logs" footnote):

```sh
docker inspect --format '{{.HostConfig.LogConfig}}' $(docker compose -f /opt/sentinelbrief/docker-compose.yml ps -q)
sudo ls -la /var/lib/docker/containers/*/*-json.log*
```

Expected: every line reads `{json-file map[max-file:3 max-size:10m]}`; rotated files appear once
a container has logged past 10 MB (expect this on `api` under attacker traffic).

```text
(recorded during deployment)
```

Honeypot host (in the root SSM session from `ec2-single-host.md` step 9's `sudo -i` — task-05
fix-2, review N2) — the same `docker inspect` check against `cowrie`, plus the journald budget:

```sh
docker inspect --format '{{.HostConfig.LogConfig}}' $(docker compose -f /opt/sentinelbrief-honeypot/docker-compose.yml ps -q)
journalctl --disk-usage
```

```text
(recorded during deployment)
```

## 9. Honeypot hardening

All commands below run in the same root SSM session as check 8's honeypot half
(`ec2-single-host.md` step 9's `sudo -i` — task-05 fix-2, review N2): `ss -ltnp`'s owning-process
column and `systemctl`/`docker` all need root, not `ssm-user`.

```sh
ss -ltnp | grep ':22 '
```

Expected: `docker-proxy`, never `sshd`.

```text
(recorded during deployment)
```

```sh
systemctl is-enabled sshd
```

Expected: `masked`.

```text
(recorded during deployment)
```

```sh
systemctl status sentinelbrief-shipper
ls /var/lib/sentinelbrief-shipper/spool
ls /var/lib/sentinelbrief-shipper/spool/dead
```

Expected: `active`; both spool directories empty (nothing queued, nothing permanently rejected).

```text
(recorded during deployment)
```

```sh
docker inspect --format '{{index .RepoDigests 0}}' sentinelbrief-honeypot-cowrie-1
```

Expected: equals the digest pinned in `honeypot/docker-compose.yml`'s `image:` line.

```text
(recorded during deployment)
```

## 10. Redis degrade/recover

```sh
docker compose stop redis
curl -s -o /dev/null -w '%{http_code}\n' $API/healthz
curl -s $API/healthz
```

Expected: `503` with `"redis":"error"` in the body.

```text
(recorded during deployment)
```

```sh
docker compose start redis
curl -s -o /dev/null -w '%{http_code}\n' $API/healthz
docker compose ps worker
```

Expected: `200`; `worker` still `running` throughout (its restart policy never had to fire because
it does not depend on Redis being reachable to stay up).

```text
(recorded during deployment)
```

## 11. Placeholders from M8

Rate limits from two real client IPs plus a forged-`X-Forwarded-For` check that stays `429`
after bucket exhaustion; SSE (`GET /api/v1/stream`) arriving unbuffered through the domain. Both
land at M8 — recorded at M8, not here.

```text
(recorded at M8)
```
