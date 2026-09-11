---
id: task-05
milestone: m6-real-data-deploy
depends_on: [task-02, task-03, task-04]
status: planned
spec: PRD.md §11 Phase 1 (the whole section — this task is its execution), §10.4/§10.9 (honeypot isolation; SSM-only, no `sshd`), §10.10 (forwarded-IP trust), §12 M6 ("Phase-1 deploy complete per `docs/deployment.md`; dashboard public at the domain; `infra/deploy/VERIFY.md` filled in with real output"), §13 (domain, instance size — owner decisions); `docs/deployment.md` (finalized HERE with real resource names, never values); `docs/plans/m6-real-data-deploy.md` (Architecture: "Owner-run steps (AWS console, DNS, SSM parameters) are documented as walkthroughs, not automated"; Global Constraints: prod copies equal the box byte-for-byte after every apply; the backup job runs before the soak; a restore is rehearsed once); `.claude/rules/infra.md`; AdvisorDesk `infra/deploy/ec2-single-host.md` + `VERIFY.md` as the templates; tasks 01–04's runbooks and files
---

# task-05 — The owner-run deploy walkthrough for both hosts (`infra/deploy/ec2-single-host.md`), the IAM/user-data artifacts, the `VERIFY.md` template, and `docs/deployment.md` finalized; then the deploy itself (owner checkpoint)

## Goal

One document an owner with an AWS account, the `aws` CLI, Docker and this repo can follow top to
bottom, in order, to bring up BOTH hosts and end with the dashboard public at the domain and the
first real attacker session `triaged`: IAM roles and the inline policy (committed as JSON), the
backup bucket + lifecycle, SSM parameters (commands with `<value>` placeholders — the values are
typed by the owner, never by an agent, never into a file), the app-host instance from a committed
user-data script, Elastic IP + two grey-cloud A records, `push_ecr.sh`, copying the prod files
onto the box through an SSM session, `fetch-secrets.sh`, the geoip one-off, the migration, `up
-d`, the honeypot host in its own VPC from task-01's runbook, the shipper from task-02's README,
the backup timer + first run + restore rehearsal from task-04, and finally `VERIFY.md` — a template
in the AdvisorDesk shape whose every check is the exact command with a block for the real output.
`docs/deployment.md` is finalized in the same commit: real resource NAMES (instance ids, EIP,
bucket, VPC ids, region, domain) appear once the owner has created them; no value that is a secret
ever does. **The agent-authored deliverable is the documents and their pins; the deploy itself (step 7 below)
is then run by the controller under the owner's explicit 2026-09-11 grant (AWS access, DNS, and the
deployments), from this machine's AWS CLI credentials, following the walkthrough exactly — every
secret generated in-shell and never printed, every resource name recorded in `docs/deployment.md`.**

## Context (read ONLY these)

- `PRD.md` §10.4, §10.9, §10.10, §11, §12 M6, §13.
- `docs/deployment.md` (you finalize it).
- `docs/plans/m6-real-data-deploy.md` — Architecture + Global Constraints.
- `.claude/rules/infra.md`.
- AdvisorDesk templates: `~/Documents/github_akanksha/AdvisorDesk/infra/deploy/ec2-single-host.md`
  (topology, "On the box", operational notes) and `VERIFY.md` (the check → expected → `(recorded
  during deployment)` block shape; the "set these once" variable prologue).
- This milestone's own runbooks (read, and link — do not duplicate their content):
  `honeypot/README.md` (task-01: honeypot host user-data, digest pin, Cowrie up),
  `honeypot/shipper/README.md` (task-02: shipper install + env file + unit),
  `infra/deploy/prod/README.md` (task-03: change procedure, migrations, geoip one-off, tag bumps),
  `infra/deploy/env-checklist.md` (task-03: every variable), `infra/deploy/database.md` (task-04:
  backups, restore, rehearsal, retention). `infra/deploy/push_ecr.sh`, `prod/fetch-secrets.sh`,
  `prod/backup.sh`, the timer/service files.
- Code you build on: `tests/test_deploy_scripts.py` (task-03) for the `bash -n` pin shape;
  `tests/test_backup_artifacts.py` (task-04) for the JSON pin shape.
- The M5 acceptance walk's shape for "paste real output" (`.superpowers/sdd/m5-queue-routing/progress.md`
  is NOT readable by agents — the controller carries the shape into the dispatch: commands run,
  output pasted verbatim, secrets never).

## Files

- Create: `infra/deploy/ec2-single-host.md` (the walkthrough), `infra/deploy/VERIFY.md` (the
  template), `infra/deploy/iam/app-host-trust.json`, `infra/deploy/iam/app-host-inline.json`,
  `infra/deploy/iam/honeypot-host-trust.json` (same trust document; kept separate so each role's
  files sit together), `infra/deploy/user-data-app.sh`, `honeypot/user-data.sh` (task-01's
  runbook step 3 block extracted verbatim into a file the console's user-data field takes;
  `honeypot/README.md` step 3 becomes "paste `honeypot/user-data.sh`")
- Create (test-author): `tests/test_deploy_docs.py`
- Modify: `docs/deployment.md` (finalize: resource-name placeholders `<recorded at deploy>` become
  a "Resources (recorded YYYY-MM-DD)" table the owner fills at deploy; the Verification section
  lists `VERIFY.md`'s check numbers), `README.md` (Deployment: the walkthrough link; Status line
  unchanged until task-06), `honeypot/README.md` (step 3 → the file)

## Interfaces

- **Consumes:** every file tasks 01–04 produced (linked by path); the domain assumption
  (`sentinelbrief.tyagiakanksha.com`, PRD §13 — the walkthrough's step 0 is "confirm the domain;
  if different, run the `sed` over the hits listed in task-03's report").
- **Produces exactly:**

  `infra/deploy/ec2-single-host.md` — numbered, owner-run, every command copy-pasteable, every
  secret a `<placeholder>`; region `us-east-1` throughout:

  0. **Prerequisites & decisions** — domain confirmed (PRD §13 — CONFIRMED by the owner 2026-09-11); AWS
     account `181040156847` (AdvisorDesk's; the honeypot goes in a SEPARATE VPC of the same account per
     PRD §10.4 — the default VPC `vpc-00735b325754614bd` 172.31.0.0/16 already hosts the AdvisorDesk
     instance, so the app host joins the default VPC and the honeypot gets its own `10.99.0.0/24` VPC); instance sizes (app `t3.small`
     x86_64, honeypot `t4g.nano` arm64 — the honeypot compose has no arch-specific image, and
     Cowrie publishes multi-arch); `aws sts get-caller-identity` works; Docker + `git` locally;
     the four secrets generated locally in the shell only (`python3 -c "import secrets;
     print(secrets.token_urlsafe(48))"` for `INGEST_HMAC_SECRET`, `ADMIN_TOKEN`,
     `POSTGRES_PASSWORD`; the LLM key from the provider; optional AbuseIPDB + MaxMind keys).
  1. **IAM** — `aws iam create-role --role-name sentinelbrief-app-host --assume-role-policy-document
     file://infra/deploy/iam/app-host-trust.json`; attach `AmazonSSMManagedInstanceCore` and
     `AmazonEC2ContainerRegistryReadOnly`; `put-role-policy … file://infra/deploy/iam/app-host-inline.json`
     (SSM `GetParameter`/`GetParameters` on `arn:aws:ssm:us-east-1:<acct>:parameter/sentinelbrief/*`,
     `kms:Decrypt` via the `aws/ssm` key, `s3:PutObject`+`s3:ListBucket` on
     `arn:aws:s3:::sentinelbrief-backups-<acct>` and `/postgres/*` — **no `"*"` Action, no `"*"`
     Resource**); instance profile; the honeypot role `sentinelbrief-honeypot-host` with
     `AmazonSSMManagedInstanceCore` ONLY (no inline policy — it must not be able to read a
     parameter or an image).
  2. **S3 backup bucket** — `aws s3api create-bucket --bucket sentinelbrief-backups-<acct>`;
     `put-public-access-block` (all four true); `put-bucket-lifecycle-configuration …
     file://infra/deploy/s3-lifecycle.json`; `put-bucket-encryption` (SSE-S3).
  3. **SSM parameters** — six `aws ssm put-parameter --type SecureString --name
     /sentinelbrief/<NAME> --value '<value>'` lines (`DATABASE_URL` =
     `postgresql://sentinel:<POSTGRES_PASSWORD>@postgres:5432/sentinelbrief` — the SAME password
     as the `POSTGRES_PASSWORD` parameter), plus the two optional ones; the reminder that the
     shell history should be cleared (`history -c`) or the commands run with a leading space
     under `HISTCONTROL=ignorespace`.
  4. **App host** — security group `sentinelbrief-app` (inbound 80, 443 from `0.0.0.0/0` and
     `::/0`; no 22); `aws ec2 run-instances --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
     --instance-type t3.small --iam-instance-profile Name=sentinelbrief-app-host
     --user-data file://infra/deploy/user-data-app.sh --block-device-mappings … 20 GiB gp3
     --tag-specifications …Name=sentinelbrief-app`; `allocate-address` + `associate-address`.
     `user-data-app.sh`: `dnf install -y docker`, compose plugin (the same pinned release as
     task-01's), `systemctl enable --now docker`, `systemctl disable --now sshd && systemctl mask
     sshd`, `mkdir -p /opt/sentinelbrief/geoip /var/backups/sentinelbrief`, `usermod -aG docker
     ssm-user`, journald `SystemMaxUse=500M`.
  5. **DNS** — two A records at Cloudflare, `sentinelbrief` and `api.sentinelbrief`, DNS-only
     (grey cloud), re-check the proxy icon after saving; `dig +short` both until they answer the
     EIP.
  6. **Images** — `AWS_ACCOUNT_ID=<acct> ./infra/deploy/push_ecr.sh` from a clean checkout of the
     commit being deployed; copy the two `:<sha>` URIs it prints into BOTH
     `infra/deploy/prod/docker-compose.yml` (the three `image:` lines; account id + SHA) — commit
     that edit on the deploy branch before copying files to the box (prod copies equal the box).
  7. **Files onto the box** — `aws ssm start-session --target <instance-id>`; in the session,
     `sudo -i`; for each of `docker-compose.yml`, `Caddyfile`, `fetch-secrets.sh`, `backup.sh`,
     `backup.env`, `restore-rehearsal.sh`, the two unit files: `cat > /opt/sentinelbrief/<name>
     <<'EOF' … EOF` (the walkthrough gives one exact heredoc per file and the `chmod` line;
     `backup.env`'s bucket name edited to the real one — and committed back).
  8. **Secrets, geoip, migrate, up** — `./fetch-secrets.sh` (expect `OK wrote 5 vars … and 1 var
     …`), `aws ecr get-login-password | docker login …`, `docker compose pull`, the geoip one-off
     from `prod/README.md`, `docker compose run --rm api uv run alembic upgrade head`, `docker
     compose up -d`, `docker compose ps` → six `healthy`/`running`.
  9. **Honeypot host** (task-02 review notes: install the shipper together with Cowrie so its first
     read of `cowrie.json` is small; before `systemctl enable --now sentinelbrief-shipper`, prove
     `sudo -u shipper head -c 1 /opt/sentinelbrief-honeypot/data/log/cowrie.json` succeeds and the
     directory exists; check `systemctl is-active sentinelbrief-shipper` AFTER 60 s, not immediately,
     so a `RestartSec=5` loop is visible) — separate VPC (`aws ec2 create-vpc 10.99.0.0/24`, subnet, IGW, route;
     or the console) — the walkthrough gives the CLI sequence; SG `sentinelbrief-honeypot`
     (inbound 22 from everywhere; outbound 443 only — `revoke-security-group-egress` the default
     all-traffic rule first); `run-instances … al2023 arm64 t4g.nano --iam-instance-profile
     Name=sentinelbrief-honeypot-host --user-data file://honeypot/user-data.sh`; EIP; then
     `honeypot/README.md` steps 4–6 and `honeypot/shipper/README.md` (the env file's two lines are
     typed in the SSM session from the owner's clipboard — the honeypot role cannot read SSM).
  10. **First real session** — from a laptop: `ssh -p 22 root@<honeypot-eip>` with any password,
      type `uname -a`, `exit`; within ~2 s: `journalctl -u sentinelbrief-shipper -n 3` shows
      `delivered … status=202`; on the app host `docker compose logs worker --since 5m | grep
      'triage'` shows the job; `curl -s https://api.sentinelbrief.<domain>/api/v1/alerts?limit=1`
      shows it `triaged`.
  11. **Backups** — `cp` the unit files to `/etc/systemd/system/`, `systemctl daemon-reload &&
      systemctl enable --now sentinelbrief-backup.timer`, `systemctl start
      sentinelbrief-backup.service && journalctl -u sentinelbrief-backup -n 3` → `backup ok
      key=…`; `aws s3 ls s3://sentinelbrief-backups-<acct>/postgres/`; `./restore-rehearsal.sh` →
      `restore ok …` (paste into `database.md`'s block).
  12. **Verify** — run `VERIFY.md` top to bottom; paste; commit `VERIFY.md`, `database.md`,
      `docs/deployment.md`'s resource table, and any prod-copy drift in one `chore(deploy): m6
      first deploy — <date>` commit.
  Plus an **Operational notes** section (redeploy cycle = `prod/README.md`; rotating a secret;
  changing the API's public origin means a web image rebuild; the honeypot host is disposable —
  terminate and re-run step 9).

  `infra/deploy/VERIFY.md` — prologue `API=https://api.sentinelbrief.tyagiakanksha.com
  WEB=https://sentinelbrief.tyagiakanksha.com`; checks, each with the command, the expected
  result, and a ```text block `(recorded during deployment)`:
  0. `curl -s $API/healthz` → `{"status":"ok","db":"ok","redis":"ok"}`.
  1. TLS + headers: `curl -sI $WEB | grep -iE 'strict-transport|x-frame|HTTP/'` → `HTTP/2 200`,
     HSTS, `DENY`; same for `$API/healthz`.
  2. Ingest gates: unsigned POST → `401 {"error":{"code":"unauthorized"…}}`; a 3 MB body
     (`head -c 3000000 /dev/zero`) → `413` (Caddy's; body `{"error"…}` or Caddy's plain 413 —
     record which); a 1.9 MB unsigned body → `413 payload_too_large` (the app's — proves the
     in-app cap independent of Caddy); a chunked unsigned POST (`-H 'Transfer-Encoding: chunked'
     -H 'Content-Length:'`) → `411`.
  3. First real session `triaged` (step 10's outputs).
  4. CORS: `Origin: https://evil.example.com` → no `access-control-allow-origin`; `Origin: $WEB` →
     header echoes `$WEB`.
  5. Dashboard: `curl -s $WEB/alerts | grep -c '<table'` ≥ 1; `curl -s $WEB/healthz` →
     `{"status":"ok"}`.
  6. **No LLM call from any public request (PRD §12 M6, §10.1)** — on the box: `docker compose
     logs api --since 24h 2>&1 | grep -ciE 'worker ready|triage job|triage attempt|triage failed|routing escalated|llm|openai'`
     → `0`; `docker compose logs worker --since 24h 2>&1 | grep -ciE 'triage job|routing escalated|worker ready'`
     → `> 0`; plus `docker compose exec -T api uv run python -c "import sys; import api.main;
     print([m for m in sys.modules if m.startswith('worker') or m == 'core.llm' or m.startswith('openai')])"`
     → `[]` (the api process never even imports the LLM client).
  7. Backups: `aws s3 ls …/postgres/` shows today's key; `journalctl -u sentinelbrief-backup -n
     2`; `restore ok …`; `docker compose exec -T postgres psql -U sentinel -d sentinelbrief -tAc
     "select pg_size_pretty(pg_database_size('sentinelbrief'))"`.
  8. Log rotation (task-04's commands, both hosts).
  9. Honeypot host: `ss -ltnp | grep ':22 '` shows `docker-proxy`, not `sshd`; `systemctl
     is-enabled sshd` → `masked`; `systemctl status sentinelbrief-shipper` → `active`; `ls
     /var/lib/sentinelbrief-shipper/spool` empty; `ls …/spool/dead` empty; `docker inspect
     --format '{{index .RepoDigests 0}}' sentinelbrief-honeypot-cowrie-1` equals the compose
     file's digest.
  10. Redis degrade/recover: `docker compose stop redis` → `/healthz` `503 …"redis":"error"`;
      `start` → `200`; `docker compose ps worker` → running (restart policy).
  11. *(From M8)* rate limits from two real IPs + forged XFF; SSE unbuffered through the domain —
      placeholders with "recorded at M8".

  `docs/deployment.md` finalized: the "Resources (recorded at deploy)" table (region, account id,
  VPC ids ×2, instance ids ×2, EIPs ×2, SG names, role names, bucket, ECR repos, domain) with
  `<recorded at deploy>` cells the owner fills in step 12; the Data section names the timer; the
  Honeypot section names the shipper paths; the Verification section lists checks 0–11.

## Interfaces → test table

`tests/test_deploy_docs.py` — text/JSON pins.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| links resolve | `::test_every_relative_link_in_deploy_docs_resolves` | for each `.md` under `infra/deploy/`, `honeypot/`, and `docs/deployment.md`: every `](path)` relative link (no `http`) exists on disk relative to the file |
| IAM least privilege | `::test_iam_documents_parse_and_have_no_wildcard_action_or_resource` | both inline/trust JSONs parse; in `app-host-inline.json` no statement has `Action == "*"`, `"*"` in an Action list, or `Resource == "*"`; every `Resource` string starts with `arn:aws:`; trust documents allow only `ec2.amazonaws.com` |
| no secret values | `::test_deploy_docs_carry_no_value_shaped_secret` | over every file under `infra/deploy/` and `honeypot/*.md`: regexes `sk-[A-Za-z0-9_-]{20,}`, `postgresql://[^<\s]+:[^<\s'"]+@` (a real password — `<POSTGRES_PASSWORD>` placeholders are exempt because `<` breaks the match), `AKIA[0-9A-Z]{16}`, 40+ hex runs → 0 hits |
| walkthrough steps | `::test_walkthrough_names_every_step_and_both_hosts` | headings for steps 0–12 present in order; contains `systemctl mask sshd` (≥1), `sentinelbrief-honeypot-host`, `sentinelbrief-app-host`, `push_ecr.sh`, `fetch-secrets.sh`, `alembic upgrade head`, `restore-rehearsal.sh`, `sentinelbrief-backup.timer`, `grey`, `history -c` |
| VERIFY template | `::test_verify_template_has_all_checks_and_placeholders` | headings `## 0.` … `## 11.` present; the no-LLM grep line present with `grep -ciE` and both `api` and `worker`; `(recorded during deployment)` count ≥ 12; contains `payload_too_large`, `411`, `pg_database_size`, `docker-proxy`, `masked` |
| user-data scripts | `::test_user_data_scripts_parse_and_disable_sshd` | `bash -n` on `infra/deploy/user-data-app.sh` and `honeypot/user-data.sh`; each file's text `startswith("#!/bin/bash")` and its second line is `set -euo pipefail` (task-01 re-review N4: `bash -n` passes a shebang-less file, and EC2 cloud-init runs nothing without `#!`); both contain `systemctl disable --now sshd` and `systemctl mask sshd`; the app one contains `/opt/sentinelbrief/geoip`; the honeypot one `chown -R 999:999` and `useradd --system` |
| deployment.md | `::test_deployment_doc_has_resource_table_and_check_list` | contains "Resources (recorded at deploy)", `<recorded at deploy>` ≥ 8 occurrences, `VERIFY.md` checks "0–11" |
| runbook cross-link | `::test_honeypot_readme_points_at_user_data_file` | `honeypot/README.md` step 3 contains `honeypot/user-data.sh` |

## Steps (TDD)

- [ ] **Step 1 (RED — test-author):** `tests/test_deploy_docs.py` per the table.
- [ ] **Step 2 (RED — test-author):** `uv run pytest -q tests/test_deploy_docs.py` → Expected:
  every test fails on missing files/headings (the link test may pass on BASE — then it is a
  regression guard and the report says so). Pin, commit `test(infra): deploy walkthrough / VERIFY
  / IAM / user-data pins RED (m6 task-05)`.
- [ ] **Step 3 (GREEN — implementer): IAM JSON + user-data scripts + `honeypot/user-data.sh`**
  (extract from `honeypot/README.md` step 3 verbatim; the README step now references the file).
  `bash -n` both; `python3 -m json.tool` each JSON.
- [ ] **Step 4 (GREEN — implementer): `ec2-single-host.md`** — every step above with its exact
  commands; **every `aws` command is reviewed for a placeholder where a value would go**.
- [ ] **Step 5 (GREEN — implementer): `VERIFY.md`** — the 12 checks; `docs/deployment.md`
  finalized; README link.
- [ ] **Step 6 (implementer): full gates (cold) → commit** `docs(deploy): owner-run walkthrough
  for both hosts, VERIFY template, IAM + user-data, deployment.md finalized (m6 task-05)`;
  path-scoped `git add infra/deploy honeypot/user-data.sh honeypot/README.md docs/deployment.md
  README.md`.
- [ ] **Step 7 — Deploy (controller-run under the owner's grant; not a subagent):** after the
  review, the controller executes steps 0–12 from this machine (`aws` CLI, us-east-1), pasting
  each command's non-secret output into the ledger as it goes; secrets (`INGEST_HMAC_SECRET`,
  `ADMIN_TOKEN`, `POSTGRES_PASSWORD`) are generated in-shell and written ONLY to SSM; the LLM key
  is read from the local `.env` into the shell, never echoed; optional keys absent locally
  (AbuseIPDB, MaxMind) are skipped and the runbook's "optional" path is followed. The two
  Cloudflare A records: added via the Cloudflare dashboard (Chrome tools) if reachable, else the
  controller asks the owner for exactly those two records and continues once `dig` resolves.
  Resource names go into `docs/deployment.md`'s table and `VERIFY.md`'s blocks; prod-copy drift
  is committed in the same sitting (`chore(deploy): m6 first deploy — <date>`). Then task-06.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_deploy_docs.py tests/test_honeypot_compose.py tests/test_backup_artifacts.py   # all pass, 0 skipped
bash -n infra/deploy/user-data-app.sh honeypot/user-data.sh && echo syntax-ok
python3 -m json.tool infra/deploy/iam/app-host-inline.json > /dev/null && echo json-ok
grep -c "(recorded during deployment)" infra/deploy/VERIFY.md    # >= 12   (BASE: file absent)
grep -rEc "AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}" infra/deploy honeypot | grep -v ':0$'   # no output
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- An owner can execute `infra/deploy/ec2-single-host.md` from a fresh AWS account to both hosts
  live, with every secret typed by them and every resource name recorded in the doc of record;
  the IAM documents grant the app host only SSM-read of `/sentinelbrief/*`, ECR pull and
  `PutObject` on the backup bucket, and the honeypot host only SSM.
- `VERIFY.md` is a complete template whose checks cover PRD §12 M6's clauses (healthz, TLS,
  ingest gates incl. the body cap, the first real session, CORS, the dashboard, the no-LLM log
  proof, backups, log rotation, honeypot hardening, Redis degrade/recover), each with a block
  for real output.
- The documents create no cloud resource; the deploy (step 7) is run by the controller under the
  owner's explicit grant, with every resource name recorded and no secret ever printed.
