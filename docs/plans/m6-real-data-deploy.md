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
  permissions only; egress is 443 to the ingest hostname and SSM endpoints.
- Production compose pins git-SHA tags; `latest` is never referenced by the box.
- `infra/deploy/prod/*` equal the box byte-for-byte after every apply; drift is committed back in
  the same sitting.
- Every production service has json-file log rotation; the backup job runs before the 48 h soak
  starts and a restore is rehearsed once.
- The shipper never blocks Cowrie and never loses a closed session while the ingest URL is down
  (spool test).
- Synthetic fixture shapes are re-checked against the first real sessions; any schema surprise
  is a PRD/SUGGESTIONS note, not a silent fixture edit.

## Tasks (briefs written at the M5 gate)

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | `honeypot/`: Cowrie compose, `assets.yaml`, host hardening notes (SSM-only, no sshd, egress) | `m6-real-data-deploy/task-01-cowrie-host.md` | M5 tag |
| 2 | Shipper: session assembler, signed POST, spool + backoff, idle flush, systemd unit, tests with a replayed Cowrie log | `m6-real-data-deploy/task-02-shipper.md` | task-01 |
| 3 | `infra/deploy/`: `push_ecr.sh`, `prod/docker-compose.yml` (six services + backup), `prod/Caddyfile`, `prod/fetch-secrets.sh`, `env-checklist.md`, static pin tests | `m6-real-data-deploy/task-03-deploy-scripts-prod-copies.md` | M5 tag |
| 4 | Backups (`pg_dump` → S3, lifecycle) + restore doc + log rotation + retention decision recorded | `m6-real-data-deploy/task-04-backups-logs-retention.md` | task-03 |
| 5 | Owner-run deploy walkthrough (`ec2-single-host.md` shape); `docs/deployment.md` finalized with real names | `m6-real-data-deploy/task-05-deploy-walkthrough.md` | tasks 2–4 |
| 6 | `VERIFY.md` executed live; 48 h soak; "no LLM in api logs" grep; fixture-vs-real schema check | `m6-real-data-deploy/task-06-verify-soak.md` | task-5 |

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

planned — briefs pending (written at the M5 gate).
