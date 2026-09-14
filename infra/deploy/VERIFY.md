# VERIFY.md — deployed verification checklist (M6, PRD §12)

**Filled in below.** Every check was run for real during the deployment session
([`ec2-single-host.md`](ec2-single-host.md) step 12) and again at the T+24 h / T+48 h soak
check-ins (`.superpowers/sdd/m6-real-data-deploy/progress.md`); the actual output is pasted into
each ```text``` block beneath its `(recorded during deployment)` marker (kept as a caption, not
deleted, so a reader can still see which line is the recorded value vs. surrounding prose), with a
provenance note (source + UTC timestamp) after the block. No output here is fabricated; a value
genuinely missing from every capture says so explicitly rather than guessing.

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
{"status":"ok","db":"ok","redis":"ok"}
```

_(VERIFY run 1, 2026-09-12T18:38:40Z.)_

## 1. TLS + security headers

Deploy-day finding (VERIFY run 1): a `HEAD` request to `$API/healthz` (`curl -sI`) answers
`HTTP/2 405` — `HEAD` is not a defined method on that route — so the api probe below uses `GET`
with the body discarded instead:

```sh
curl -sI $WEB | grep -iE 'strict-transport|x-frame|HTTP/'
curl -s -o /dev/null -D - $API/healthz | grep -iE 'strict-transport|x-frame|HTTP/'
```

Expected: both print `HTTP/2 200`, a `Strict-Transport-Security` (HSTS) header, and
`X-Frame-Options: DENY`.

```text
(recorded during deployment)
HTTP/2 307
content-security-policy: frame-ancestors 'none'
referrer-policy: strict-origin-when-cross-origin
strict-transport-security: max-age=31536000; includeSubDomains
x-content-type-options: nosniff
x-frame-options: DENY
HTTP/2 200
strict-transport-security: max-age=31536000; includeSubDomains
x-frame-options: DENY
```

_(WEB: VERIFY run 1, 2026-09-12T18:38:40Z, verbatim — `$WEB` root redirects 307, hence not a bare
200. API: transcribed from the TLS-LIVE check, 2026-09-12 ~18:37 UTC, after the `GET` correction
above — the original `-sI`/`HEAD` attempt at VERIFY run 1 recorded `HTTP/2 405`, the finding that
prompted the command change.)_

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
401
```

_(VERIFY run 1, 2026-09-12T18:38:40Z.)_

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
401
```

_(VERIFY run 1, 2026-09-12T18:38:40Z — under the cap, so the signature check answers, not the
byte-count guard.)_

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
HTTP/2 413
{"error":{"code":"payload_too_large","message":"body exceeds 2000000 bytes"}}
```

_(VERIFY run 1, 2026-09-12T18:38:40Z — the app answered first, confirming the expectation above.)_

A SMALL chunked unsigned POST has no usable `Content-Length` — the app's guard fails closed:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/v1/alerts -H 'content-type: application/json' -H 'Transfer-Encoding: chunked' -d '{}'
```

Expected: `411 length_required`.

```text
(recorded during deployment)
411
```

_(VERIFY run 1, 2026-09-12T18:38:40Z.)_

A 3 MB CHUNKED body: no `Content-Length` is declared. **Deploy-day finding** (VERIFY run 1,
2026-09-12): the APP's own `411 length_required` guard answers before Caddy's own `request_body {
max_size 2MB }` ever gets a chance to cut the stream — the app rejects on the missing
`Content-Length` header immediately, so Caddy's byte cap is **not observable from the client** in
this case (the original expectation below — "Caddy's own error page" — never materializes; the
app always answers first). Captured with `-i` so the recorded envelope shows which layer actually
answered:

```sh
head -c 3000000 /dev/zero > /tmp/body-3mb.bin
curl -s -i -X POST $API/api/v1/alerts -H 'content-type: application/json' -H 'Transfer-Encoding: chunked' --data-binary @/tmp/body-3mb.bin
```

Expected: the app's `HTTP/2 411` with the `length_required` JSON envelope (not a Caddy-native
error page — see the finding above).

```text
(recorded during deployment)
HTTP/2 411
{"error":{"code":"length_required","message":"Content-Length required"}}
```

_(VERIFY run 1, 2026-09-12T18:38:40Z.)_

## 3. First real session `triaged`

Paste [`ec2-single-host.md`](ec2-single-host.md) step 10's three outputs here: the shipper's
`delivered ... status=202` line, the worker's `triage` log line, and the alert's
`"status": "triaged"` from `$API/api/v1/alerts`. Recorded below is the **CHECK 3 re-run** at the
T+48 h soak boundary — a fresh SSH session driven at the end of the 48 h acceptance clock, proving
the pipeline still works end to end after two days of live attacker traffic (the original
deploy-day capture, step 10, 2026-09-12 18:49 UTC, delivered six real sessions the same way and is
recorded in `ec2-single-host.md` step 10's own note).

```text
(recorded during deployment)
shipper (honeypot journal): Sep 14 18:58:36 … sentinelbrief-shipper[30043]: INFO sentinelbrief_shipper.main shipper: delivered session_id=617f6ff2d237 status=202 bytes=3854
worker (app): 18:58:42:   6.17s ← triage:518a17ef-0e77-4875-adcd-8b85316285a4:triage_alert ● 'triaged'
public API (GET /api/v1/alerts?page_size=1): alert 518a17ef-0e77-4875-adcd-8b85316285a4 "status": "triaged", severity 4, category successful_intrusion, escalated: true
```

_(CHECK 3 re-run, 2026-09-14T18:58:35Z — a fresh SSH session from a throwaway alpine+sshpass
container against the honeypot; Cowrie accepted the login, refused exec ("exec request failed on
channel 0"); total_alerts went 284 → 285 in ≈ 35 s end to end.)_

## 4. CORS allow/deny

```sh
curl -sI -H 'Origin: https://evil.example.com' $API/healthz | grep -i access-control-allow-origin
curl -sI -H "Origin: $WEB" $API/healthz | grep -i access-control-allow-origin
```

Expected: the first prints nothing (no `access-control-allow-origin` header); the second echoes
`$WEB`.

```text
(recorded during deployment)
evil origin: (nothing — no access-control-allow-origin header)
site origin: access-control-allow-origin: https://sentinelbrief.tyagiakanksha.com
```

_(VERIFY run 1, 2026-09-12T18:38:40Z.)_

## 5. Dashboard

```sh
curl -s $WEB/alerts | grep -c '<table'
curl -s $WEB/healthz
```

Expected: the count is `>= 1`; the healthz body is `{"status":"ok"}`.

```text
(recorded during deployment)
/alerts tables: 0
web /healthz: {"status":"ok"}
root: 307 -> https://sentinelbrief.tyagiakanksha.com/alerts
```

_(VERIFY run 1, 2026-09-12T18:38:40Z — 0 alerts existed yet, so the dashboard rendered its empty
state with no `<table` until data arrived; by T+48 h the dashboard carried 284 alerts.)_

## 6. No LLM call from any public request (PRD §12 M6, §10.1)

**Soak finding** (T+24 h / T+48 h re-runs): the original per-job worker grep pattern (`triage
job|routing escalated|worker ready`) never matched the worker's actual ARQ per-job log line shape,
so it always read `0` even while triage jobs were running — a false negative on the "`> 0`" half of
this check. `chat/completions` (the LLM client's own log line) is the pattern that actually
discriminates; the api-side grep is narrowed to match, for symmetry, and run the same way at both
the T+24 h and T+48 h check-ins.

On the box:

```sh
docker compose logs api --since 48h 2>&1 | grep -ciE 'chat/completions|api.openai.com'
```

Expected: `0` — the api process never logs an LLM call.

```text
(recorded during deployment)
0
```

_(CHECK 6, T+48 h re-run, 2026-09-14T18:58Z — 'chat/completions' 0, 'api.openai.com' 0, over the
full 48 h soak window.)_

```sh
docker compose logs worker --since 48h 2>&1 | grep -ciE 'chat/completions'
```

Expected: `> 0` — the worker is the only process doing triage work.

```text
(recorded during deployment)
295
```

_(CHECK 6, T+48 h re-run, 2026-09-14T18:58Z — 295 hits across the 48 h soak window; the worker also
logged 580 per-job health/completion lines in the same window.)_

Prove the api process never even *imports* the LLM client:

```sh
docker compose exec -T api uv run python -c "import sys; import api.main; print([m for m in sys.modules if m.startswith('worker') or m == 'core.llm' or m.startswith('openai')])"
```

Expected: `[]`.

```text
(recorded during deployment)
[]
```

_(CHECK 6, T+48 h re-run, 2026-09-14T18:58Z.)_

## 7. Backups

```sh
aws s3 ls s3://sentinelbrief-backups-181040156847/postgres/
journalctl -u sentinelbrief-backup -n 2
```

Expected: today's `sentinelbrief-<UTC stamp>.sql.gz` key; the journal's last line reads
`backup ok key=... bytes=...`.

```text
(recorded during deployment)
aws s3 ls (T+48 h, 2026-09-14T18:58:08Z):
2026-09-12 14:13:30       1473 sentinelbrief-20260912T181328Z.sql.gz
2026-09-12 23:22:24      14229 sentinelbrief-20260913T032223Z.sql.gz
2026-09-13 23:18:25      44819 sentinelbrief-20260914T031823Z.sql.gz
journalctl -u sentinelbrief-backup -n 2 (T+24 h, 2026-09-13T19:03Z — the last verbatim capture in the ledger):
Sep 13 03:22:24 ip-172-31-93-34.ec2.internal backup.sh[383625]: backup ok key=postgres/sentinelbrief-20260913T032223Z.sql.gz bytes=14229
Sep 13 03:22:24 ip-172-31-93-34.ec2.internal systemd[1]: Finished sentinelbrief-backup.service - SentinelBrief nightly Postgres backup to S3.
(the 2026-09-14T03:18Z run's exact journal line is not captured verbatim — see the ledger; its success is confirmed by the S3 object above, 44,819 B)
```

_(Three nightly dumps present in S3 at soak end, growing with the data.)_

```sh
./restore-rehearsal.sh
```

Expected: `restore ok alerts=... verdicts=... tool_calls=... alembic=...` (paste the same line
into [`database.md`](database.md)'s own block).

```text
(recorded during deployment)
restore ok alerts=0 verdicts=0 tool_calls=0 alembic=0001
```

_(deploy day, 2026-09-12 — run once during the walkthrough, step 11, before any honeypot session
existed yet; also pasted into `database.md`.)_

```sh
docker compose exec -T postgres psql -U sentinel -d sentinelbrief -tAc "select pg_size_pretty(pg_database_size('sentinelbrief'))"
```

Expected: a human-readable size (e.g. `12 MB`) — feeds `database.md`'s retention decision.

```text
(recorded during deployment)
8919 kB
```

_(T+48 h, 2026-09-14T18:58Z — `pg_database_size('sentinelbrief')`; was 8,351 kB at T+24 h,
2026-09-13T19:03Z.)_

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
sentinelbrief-caddy-1 json-file map[max-file:3 max-size:10m]
sentinelbrief-web-1 json-file map[max-file:3 max-size:10m]
sentinelbrief-worker-1 json-file map[max-file:3 max-size:10m]
sentinelbrief-api-1 json-file map[max-file:3 max-size:10m]
sentinelbrief-redis-1 json-file map[max-file:3 max-size:10m]
sentinelbrief-postgres-1 json-file map[max-file:3 max-size:10m]
```

_(`docker inspect`: T+24 h, 2026-09-13T19:03Z. At T+48 h, 2026-09-14T18:58Z: 6 log files totaling
1,430,202 bytes, none rotated yet — the config is unchanged and the largest file is still under
the 10 MB threshold after 2 days.)_

Honeypot host (in the root SSM session from `ec2-single-host.md` step 9's `sudo -i` — task-05
fix-2, review N2) — the same `docker inspect` check against `cowrie`, plus the journald budget:

```sh
docker inspect --format '{{.HostConfig.LogConfig}}' $(docker compose -f /opt/sentinelbrief-honeypot/docker-compose.yml ps -q)
journalctl --disk-usage
```

```text
(recorded during deployment)
docker inspect: not independently captured during a soak check-in (not captured — see ledger);
honeypot/docker-compose.yml pins the same json-file driver (max-size 10m, max-file 3) for cowrie
via the shared x-logging anchor, unchanged since deploy.
journalctl --disk-usage (T+24 h, 2026-09-13T19:03Z):
Archived and active journals take up 16.0M in the file system.
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
docker-proxy owns 0.0.0.0:22 and [::]:22
```

_(deploy step 9e, 2026-09-12.)_

```sh
systemctl is-enabled sshd
```

Expected: `masked`.

```text
(recorded during deployment)
masked
```

_(deploy steps 9d/9, 2026-09-12 — confirmed again at T+24 h and T+48 h.)_

```sh
systemctl status sentinelbrief-shipper
ls /var/lib/sentinelbrief-shipper/spool
ls /var/lib/sentinelbrief-shipper/spool/dead
```

Expected: `active`; both spool directories empty (nothing queued, nothing permanently rejected).

```text
(recorded during deployment)
active
spool (pending): 0
spool/dead: 0
```

_(deploy step 10, 2026-09-12 18:49 UTC — the six real sessions spooled during DNS negative-caching
drained after the shipper restart; reconfirmed at T+48 h, 2026-09-14T18:58Z: spool 0, dead 0,
NRestarts 0.)_

```sh
docker inspect --format '{{index .RepoDigests 0}}' sentinelbrief-honeypot-cowrie-1
```

Expected: equals the digest pinned in `honeypot/docker-compose.yml`'s `image:` line.

```text
(recorded during deployment)
sha256:42e01e0e…740d44
```

_(deploy step 9, 2026-09-12 — container `Config.Image` and the image `RepoDigest` agree, matching
the pin in `honeypot/docker-compose.yml`.)_

## 10. Redis degrade/recover

```sh
docker compose stop redis
curl -s -o /dev/null -w '%{http_code}\n' $API/healthz
curl -s $API/healthz
```

Expected: `503` with `"redis":"error"` in the body.

```text
(recorded during deployment)
503
{"status":"degraded","db":"ok","redis":"error"}
```

_(VERIFY check 10, 2026-09-12.)_

```sh
docker compose start redis
curl -s -o /dev/null -w '%{http_code}\n' $API/healthz
docker compose ps worker
```

Expected: `200`; `worker` still `running` throughout (its restart policy never had to fire because
it does not depend on Redis being reachable to stay up).

```text
(recorded during deployment)
200
worker: restarted by its unless-stopped policy (health: starting -> healthy) once Redis came back
— NOT "still running throughout" as the Expected line above assumes; see the M6 doc-pass
implementer report for this discrepancy. Public healthz ok afterwards.
```

_(VERIFY check 10, 2026-09-12.)_

## 11. Placeholders from M8

Rate limits from two real client IPs plus a forged-`X-Forwarded-For` check that stays `429`
after bucket exhaustion; SSE (`GET /api/v1/stream`) arriving unbuffered through the domain. Both
land at M8 — recorded at M8, not here.

```text
(recorded at M8)
```
