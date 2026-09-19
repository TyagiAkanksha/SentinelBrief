---
id: task-04
milestone: m8-polish
depends_on: [m7-tag]
status: planned
spec: PRD.md §8 (per-IP rate limits are in-app, Redis-backed, `429` enveloped; Caddy does none), §10 (retriage admin-token-gated and globally capped; no public request path triggers an LLM call), §10.10 (forwarded-IP trust only under Caddy+unpublished api), §12 M8; `.claude/rules/api.md` (public GETs never compute; retriage admin-gated); M5 task-04 deferral M5 ("the retriage flip needs `SET LOCAL lock_timeout` + 409") and M4 ("pub/sub is a hint; the SSE consumer re-reads the DB")
---

# task-04 — Redis-backed per-IP rate limiter on public GETs + an admin-token-gated, globally-capped retriage route

## Goal
Every public GET (`/api/v1/alerts`, `/api/v1/alerts/{id}`, `/api/v1/stats`, `/api/v1/stream`) is
rate-limited per client IP by a Redis fixed-window counter: over `PUBLIC_RATE_LIMIT_PER_MIN` in a
rolling minute → `429` with the standard `{"error":{"code":"rate_limited","message":...}}` envelope
and a `Retry-After` header. `POST /api/v1/alerts` (HMAC-signed ingest) is NOT public-rate-limited
(it has its own auth + body cap). A new `POST /api/v1/admin/retriage/{alert_id}` requires the
`ADMIN_TOKEN` bearer, is capped at `RETRIAGE_PER_DAY` (20) across ALL admins/day (a global Redis
counter, not per-IP), flips a `triaged`/`failed` alert back to `pending` under `SET LOCAL
lock_timeout` (409 `conflict` if the row is locked), and re-enqueues it — the ONLY non-nightly path
that causes an LLM call, and it never runs from an unauthenticated request. `VERIFY.md` gains the
two-real-IP rate-limit check, the forged-XFF retry (stays 429 / never spoofable), and the retriage
401/200/429 walk.

## Context (read ONLY these)
- `PRD.md` §8, §10, §10.10, §12 M8. `docs/plans/m8-polish.md` Global Constraints. `.claude/rules/api.md`.
- Code: `api/factory.py` (`create_app`, where a middleware/dependency is wired), `api/deps.py`
  (`get_settings`, `get_session`, `get_enqueue`; add `require_admin_token`, `rate_limit`),
  `api/routes/alerts_read.py` + `stream.py` (the public GETs), `api/routes/alerts.py` (ingest —
  NOT rate-limited), `core/config.py` (add the settings), `core/errors.py`
  (`RateLimitedError` exists; add `ConflictError` if absent, reuse for 409), `api/errors.py`
  (`STATUS_BY_ERROR` — map `rate_limited`→429 with `Retry-After`), `worker/` enqueue seam
  (`EnqueueFn`), the M5 idempotency job (`FOR UPDATE`, skip-when-not-pending) which retriage reuses.
- The M5 lock: retriage must `SET LOCAL lock_timeout = '<ms>'` then `SELECT ... FOR UPDATE` the
  alert row; on lock timeout raise `ConflictError` (409). It flips status→pending, commits, then
  enqueues (the row is durable before the job — spine M5-a).

## Interfaces
```python
# core/config.py
public_rate_limit_per_min: Annotated[int, Field(ge=0)] = 60   # 0 = unlimited (dev default may stay 60)
retriage_per_day: Annotated[int, Field(ge=1)] = 20
admin_token: SecretStr = SecretStr("")
retriage_lock_timeout_ms: Annotated[int, Field(ge=1)] = 3000
# api/deps.py
async def rate_limit(request: Request, settings=Depends(get_settings), redis=Depends(get_redis)) -> None
    # key = f"rl:{client_ip}:{minute_bucket}"; INCR + EXPIRE 60 (pipeline); > limit → RateLimitedError(retry_after)
    # client_ip from request.client.host (Caddy overwrote XFF; api never host-published — PRD §10.10);
    # when public_rate_limit_per_min == 0, no-op. Redis DOWN → fail-open (log once), never 500 a GET.
async def require_admin_token(request: Request, settings=Depends(get_settings)) -> None
    # Authorization: Bearer <ADMIN_TOKEN>; hmac.compare_digest; empty ADMIN_TOKEN → 401 always (never fail-open); missing/bad → SignatureError-style 401
# api/routes/admin.py  (new)
# POST /api/v1/admin/retriage/{alert_id}  operation_id="retriage_alert", deps=[require_admin_token]
#   global daily cap: INCR "retriage:{utc_date}" ; > RETRIAGE_PER_DAY → RateLimitedError(429) BEFORE any DB work
#   load alert; not triaged/failed → 409 conflict; SET LOCAL lock_timeout + FOR UPDATE → pending; commit; enqueue
#   202 {"id","status":"pending","retriaged":true}
```
Wire `rate_limit` as a dependency on the public GET routers only (not ingest, not admin, not `/healthz`). `retriage` regenerates `api/openapi.json` + `web` codegen in the same commit.

## Folds these deferred findings (from `.superpowers/sdd/m7-eval-hardening/whole-branch-deferred.md` + the M8a final review)
- Confirm `api/errors.py` `Retry-After` on 429 (new).
- M8a N-M1/N-M2 (unsubscribe-raises-skips-aclose class-only callback), N2 (pin the settle ref-read), header gutter alignment, PrimaryNav render test + t02 M4 — these are `web/` and go to task-06 or here if a nav file is touched; leave to task-06 unless incidental.

## Steps (TDD outline)
- test-author: `tests/test_rate_limit.py` (INCR/EXPIRE window, 429+Retry-After, fail-open on Redis down, ingest exempt, 0=unlimited), `tests/test_admin_retriage.py` (401 no/bad token, 401 empty ADMIN_TOKEN, 202 flips pending + enqueues, 409 not-triaged/failed, 409 on lock timeout, 429 on the 21st/day), `tests/test_admin_auth.py`. Redis fixtures skip by name without `TEST_REDIS_URL`.
- implementer: the settings + `.env.example` + env-checklist rows, `rate_limit`/`require_admin_token` deps, `api/routes/admin.py`, wiring in `factory.py`, OpenAPI + codegen baselines.

## Verify (VERIFY.md additions — run at deploy)
- Two real IPs: each gets `200`s up to the limit then `429`s in its own bucket.
- Forged `X-Forwarded-For: 1.2.3.4` from one IP still counts against the real IP's bucket and stays `429` (Caddy overwrote it; api sees the real peer).
- Retriage: no token → 401; bad token → 401; valid token on a triaged alert → 202 + a new verdict row after the job; 21st call in a UTC day → 429.

## Acceptance
Per-IP limits enforced in-app with the envelope + Retry-After; the forged-XFF check passes; retriage is admin-gated, globally capped at 20/day, lock-safe (409), and is the only non-nightly LLM trigger; OpenAPI + codegen regenerated.
