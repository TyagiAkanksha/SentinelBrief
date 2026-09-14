# m6-real-data-deploy — Real data + Phase-1 deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M6 — **authoritative.** Primary sections: §3 (honeypot isolation
invariant), §10 (all guardrails; items 4, 8, 9, 10 are deploy-time), §11 Phase 1 (the topology),
§6.1 (the shipper's contract with ingest), §1.2 (one alert per session). Doc of record:
[`../deployment.md`](../deployment.md).
**Conventions:** `CONVENTIONS.md` §11 · `.claude/rules/infra.md` · AdvisorDesk's
`infra/deploy/*` as templates (`push_ecr.sh`, `prod/docker-compose.yml`, `prod/Caddyfile`,
`prod/fetch-secrets.sh`, `env-checklist.md`, `VERIFY.md`, `ec2-single-host.md`).

**Goal:** a Cowrie honeypot host live on its own VPC/account, SSM-managed with no `sshd`; a
shipper that assembles sessions and POSTs signed alerts on close (with a local spool); the app
host deployed per `docs/deployment.md` (ECR images by git SHA, SSM Parameter Store secrets,
Caddy TLS, six containers, backups, log rotation); the dashboard public at the domain; 48 h of
real attacker traffic visible; `infra/deploy/VERIFY.md` filled with real output; the "no LLM call
from any public request" log check passed.

**Architecture:** `honeypot/` holds a Cowrie compose file (official image, JSON log to a
volume), `assets.yaml`, and `shipper/` — a small Python program (stdlib + httpx, packaged with its
own tiny pyproject so the honeypot host installs nothing else) that tails `cowrie.json`, groups
by `session`, flushes on `cowrie.session.closed` or idle timeout, signs with `core/signing.py`'s
algorithm (vendored copy — the honeypot never has the repo's secrets or DB access), spools to
disk on failure and drains in order. `infra/deploy/` gets the AdvisorDesk-shaped scripts and the
committed production copies; `docs/deployment.md` is finalized with real resource names (never
secret values). Owner-run steps (AWS console, DNS, SSM parameters) are documented as
walkthroughs, not automated.

**Tech Stack:** M5 stack + Cowrie (Docker) · systemd · AWS ECR / EC2 / SSM / S3 · Caddy 2 ·
Cloudflare DNS (grey-cloud).

## Global Constraints

M0–M5 Global Constraints apply verbatim (branch `feat/m6-real-data-deploy`). Additionally:

- **Bound the ingest request body (M2 final review I1).** The signed ingest reads the whole body before verifying the HMAC and nothing bounds it today. The prod Caddyfile sets `request_body { max_size 2MB }` on the api site, and the shipper brief states the per-session payload cap it enforces; an in-app `Content-Length` guard (413 envelope, checked before `request.body()`) lands with the shipper task so the api never buffers an unbounded unauthenticated body. Pinned by a test that posts an oversized body.
- **No secret value in the repo, ever** — scripts and docs name variables and SSM paths only.
  `.env` on the box is rendered by `fetch-secrets.sh`; the honeypot host holds only
  `INGEST_HMAC_SECRET`.
- The honeypot host shares nothing else with the app host; its instance role has SSM core
  permissions only; egress is 443 to the ingest hostname and SSM endpoints. *(Both halves of this
  constraint were wrong as written — see rulings R17 and R19 below: the managed policy allows
  `ssm:GetParameter` on `*` and the explicit Deny is the control, and the SG allows 443 to
  `0.0.0.0/0`.)*
- Production compose pins git-SHA tags; `latest` is never referenced by the box.
- `infra/deploy/prod/*` equal the box byte-for-byte after every apply; drift is committed back in
  the same sitting.
- Every production service has json-file log rotation; the backup job runs before the 48 h soak
  starts and a restore is rehearsed once.
- The shipper never blocks Cowrie and never loses a closed session while the ingest URL is down
  (spool test).
- Synthetic fixture shapes are re-checked against the first real sessions; any schema surprise
  is a PRD/SUGGESTIONS note, not a silent fixture edit.
- **The canonical pytest gate includes `--cov=sentinelbrief_shipper` from task-02 fix-1 on** (review
  PC2/M6: the milestone's largest new package must be inside the coverage gate) — CI, `/gates`,
  CONVENTIONS §9 and every brief's Verify block carry the flag; the 90 % floor applies to the union.
- **Every fenced command in an owner-run runbook has been executed by the implementer in the exact
  form written, or is annotated in the runbook with why it cannot be run here** (task-01 review:
  three Important findings were commands never run as written — a shell-less image, a user-data
  block without `#!`, a bind source never created). The implementer's report lists each runbook
  command with "ran" or "cannot run here: <why>".

Briefing rulings (2026-09-11, recorded here so no task has to re-derive them; each names its cost
if wrong):

- **Cowrie's `var/` is bind-mounted, not a named volume (task-01).** `./data/log` and `./data/lib`
  under `/opt/sentinelbrief-honeypot/` so the shipper runs as an unprivileged host user and reads
  the log at a plain path; `honeypot/data/` is already gitignored and dockerignored. Cost if
  wrong: two compose lines and one `chown` in user-data.
- **The ingest body cap is a declared-length check on `SignedRoute` (task-02):** `Content-Length`
  above `INGEST_MAX_BODY_BYTES` (Setting, default 2 000 000) → `413 payload_too_large`; no usable
  `Content-Length` → `411 length_required`; both BEFORE the signature reads the body; Caddy's
  `request_body { max_size 2MB }` (task-03) enforces the real byte count on the wire. The shipper
  caps its own payload at `SHIPPER_MAX_PAYLOAD_BYTES` (1 500 000) by dropping middle events and
  records `shipper.truncated_events` in the envelope. Cost if wrong: two error classes and one
  Setting.
- **The shipper is its own tiny package (`honeypot/shipper/`, `httpx` only), imported by the
  test suite through pytest `pythonpath`, type-checked under mypy strict, and isolated from the
  repo by an AST test** (no `core`/`api`/`worker`/`evals` import; the signing algorithm is a
  vendored copy pinned equal to `core/signing.py`). Cost if wrong: a packaging change.
- **Backups run from a host systemd timer, not a cron container (task-04).** Root + the instance
  role, `pg_dump | gzip` through `docker compose exec`, S3 lifecycle 30 days, restore rehearsed
  into a scratch database; PRD §11 and `docs/deployment.md` amended (v1.5). Cost if wrong: one
  compose service replaces the timer.
- **Restart policies (M5 walk finding N-W1, task-03):** production `unless-stopped` on all six
  services; dev `on-failure` on all five (covers the ARQ worker exiting when Redis vanishes,
  without auto-starting the dev stack at boot). Cost if wrong: one word per service.
- **`REDIS_URL` (D12/N-M10, task-03):** pinned in the production compose as a non-secret
  (`redis://redis:6379/0`, no password on the compose network); `Settings` keeps it `SecretStr`
  because a URL MAY carry a password; `docs/deployment.md` says both. `LLM_TIMEOUT_S` replaces
  the `worker/llm_client.py:94` literal in the same task.
- **Domain (PRD §13 working assumption):** `sentinelbrief.tyagiakanksha.com` /
  `api.sentinelbrief.tyagiakanksha.com` throughout `infra/deploy/`; the owner confirms at task-05
  step 0; a change is one `sed` over the hits task-03's report lists.
- **The deploy is controller-run under the owner's explicit grant (2026-09-11: "explicitly
  allowing you to access aws and dns as needed and do deployments as well"; domain confirmed).**
  Tasks 01–05 still create no cloud resource while their documents are written and reviewed;
  task-05 step 7 is then executed by the controller from this machine's AWS CLI credentials
  (us-east-1), following `infra/deploy/ec2-single-host.md` exactly, with fresh production
  secrets generated in-shell and never printed, and the two Cloudflare A records added through the
  dashboard (no API token exists locally) or by the owner on request. Cost if wrong: the
  resources are small (t3.small + t4g.nano) and every step is reversible by termination.

### Gate rulings (2026-09-14, from the whole-branch review)

- **R17 — the "SSM core only ⇒ cannot read a parameter" premise in this spine and in the task-01/05
  briefs was false.** `AmazonSSMManagedInstanceCore` grants `ssm:GetParameter` on `*`; both instance
  roles could read any SecureString in the account (confirmed with `simulate-principal-policy`).
  Explicit Deny inline policies (`infra/deploy/iam/honeypot-host-deny.json`, `app-host-deny.json`)
  are the control: the honeypot reads no parameter, the app host only `/sentinelbrief/*`. Applied
  live before the docs were corrected.
- **R18 — rotation:** `ADMIN_TOKEN` is rotated at the redeploy; `LLM_API_KEY` is the owner's call.
- **R19 — egress:** the honeypot SG allows TCP 443 to any address in v1 (the ingest origin and the
  SSM endpoints); VPC endpoints are deferred; the IAM deny is the compensating control (PRD v1.6).
- **R16 — docs as records:** the two template-marker pins are amended; `VERIFY.md` and
  `database.md` carry the recorded values without template captions.

## Tasks (briefs written at the M5 gate, 2026-09-11)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | `honeypot/`: Cowrie compose, `assets.yaml`, host hardening notes (SSM-only, no sshd, egress) | `m6-real-data-deploy/task-01-cowrie-host.md` | M5 tag |
| 2 | Shipper: session assembler, signed POST, spool + backoff, idle flush, systemd unit, tests with a replayed Cowrie log | `m6-real-data-deploy/task-02-shipper.md` | task-01 |
| 3 | `infra/deploy/`: `push_ecr.sh`, `prod/docker-compose.yml` (six services; backups are a host timer, task-04), `prod/Caddyfile`, `prod/fetch-secrets.sh`, `env-checklist.md`, static pin tests; carried M5 items (restart policies, `REDIS_URL` classification, `LLM_TIMEOUT_S`) | `m6-real-data-deploy/task-03-deploy-scripts-prod-copies.md` | M5 tag |
| 4 | Backups (`pg_dump` → S3 from a systemd timer, lifecycle) + restore doc + rehearsal script + log-rotation observation commands + retention decision recorded | `m6-real-data-deploy/task-04-backups-logs-retention.md` | task-03 |
| 5 | Owner-run deploy walkthrough for both hosts (`ec2-single-host.md` shape), IAM + user-data artifacts, `VERIFY.md` template; `docs/deployment.md` finalized; **then the deploy — owner checkpoint** | `m6-real-data-deploy/task-05-deploy-walkthrough.md` | tasks 2–4 |
| 6 | `scripts/check_real_sessions.py` (fixture-vs-real schema report); `VERIFY.md` executed live; 48 h soak; "no LLM in api logs" grep; acceptance walk; housekeeping rows | `m6-real-data-deploy/task-06-verify-soak.md` | task-5 |

Order: (1 → 2) and (3 → 4) in parallel → 5 → 6. Rationale: the honeypot side and the app-host
side are independent until the walkthrough joins them; verification is last and is the
acceptance evidence.

## Acceptance walk (PRD §12 M6)

| Clause | Demonstrated by |
|---|---|
| Cowrie VM live; shipper POSTs real sessions | task-06: `alerts` rows with `source=cowrie` from the honeypot's IP appear; shipper journal excerpt |
| Phase-1 deploy complete; dashboard public at the domain | `VERIFY.md` checks 0–4 pasted (healthz, TLS, SSE unbuffered, CORS) |
| 48 h of real attacker traffic visible publicly | stats endpoint and a dashboard screenshot after 48 h; row counts pasted |
| No LLM call originates from any public request (verified in logs) | `docker compose logs api` grep for the LLM client log line = 0 hits; the same grep on `worker` > 0 |
| (v1.1) backups and log rotation observed working | S3 object listing + rotated log files pasted |

## Status

**Deployed 2026-09-12; 48 h soak passed 2026-09-14** (ledger `.superpowers/sdd/m6-real-data-deploy/progress.md`: acceptance walk, T+0/T+24/T+48 data points, doc pass e53f99c). Gate in progress: whole-branch review on the strongest model, fix wave, image bump + redeploy, PR, tag `m6`.

Earlier: in progress — briefs written 2026-09-11 (`m6-real-data-deploy/task-01` … `task-06`; they fold the
16 "Plan defects for the M6 briefing" rules from the M5 final review and the ledgered M5 → M6
items); git history and the ledger (`.superpowers/sdd/m6-real-data-deploy/progress.md`) are
authoritative.
