---
id: task-03
milestone: m2-service-persistence
depends_on: [task-02]
status: planned
spec: PRD.md §6.1 (signature-before-parse, fingerprint, ON CONFLICT, 202/200/401), §8, §10.5; CONVENTIONS.md §3, §4
---

# task-03 — HMAC signing, alert service with dedup, `POST /api/v1/alerts`

## Goal

`core/signing.py` signs and verifies request bodies (stdlib HMAC-SHA256, constant-time,
fail-closed); `core/services/alerts.py::insert_alert` inserts with `ON CONFLICT (fingerprint) DO
NOTHING` and reports whether the row was created; `POST /api/v1/alerts` verifies the signature
over the raw body **before** parsing, returns `401` / `422` / `202` (created) / `200` (duplicate),
and calls the injected `TriageFn` only for created alerts (a fake in this task's tests).

## Context (read ONLY these)

- `PRD.md` §6.1, §8 (route table + envelope), §10.5.
- `CONVENTIONS.md` §3 (services never commit; the M2 route-commit carve-out), §4.
- `.claude/rules/api.md`.
- `api/deps.py`, `api/factory.py`, `core/errors.py` (task-02); `core/models/alerts.py`
  (task-01); `core/schemas/alert.py` (M0).

## Files

- Create: `core/signing.py`, `core/services/alerts.py`, `core/schemas/ingest.py`,
  `api/routes/alerts.py`, `scripts/post_alert.py`
- Create: `tests/test_signing.py`, `tests/test_alert_service.py`, `tests/test_ingest.py`,
  `tests/test_post_alert.py`
- Modify: `api/deps.py` (+ `require_signature`), `api/factory.py` (include the alerts router
  under `/api/v1`), `api/openapi.json` (regenerated)

## Interfaces

- **Consumes:** `AlertRow`, `AlertStatus`, `SessionAlert`, `SignatureError`, `NotFoundError`,
  `TriageFn`, `SessionDep` (the `Annotated[AsyncSession, Depends(get_session, scope="function")]` alias — never bare `Depends(get_session)`), `get_settings`, `get_triage`, `Settings.ingest_hmac_secret`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/signing.py  (stdlib only)
  SIGNATURE_HEADER = "X-Signature"
  def sign_body(secret: str, body: bytes) -> str: ...                    # "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
  def verify_signature(secret: str, body: bytes, header: str | None) -> bool: ...
      # False when secret == "" or header is None/malformed (no "sha256=" prefix, non-hex); else hmac.compare_digest

  # api/deps.py addition
  async def require_signature(request: Request, settings: Settings = Depends(get_settings)) -> None: ...
      # body = await request.body(); if not verify_signature(settings.ingest_hmac_secret.get_secret_value(), body, request.headers.get(SIGNATURE_HEADER)): raise SignatureError("missing or invalid signature")

  # core/services/alerts.py
  @dataclass(frozen=True)
  class IngestResult:
      alert_id: uuid.UUID; created: bool; status: AlertStatus
  async def insert_alert(session: AsyncSession, alert: SessionAlert) -> IngestResult: ...
      # stmt = pg_insert(AlertRow).values(fingerprint=alert.fingerprint(), source=alert.source, event_time=alert.connect_time,
      #        raw=alert.model_dump(mode="json")).on_conflict_do_nothing(index_elements=["fingerprint"]).returning(AlertRow.id)
      # new_id = (await session.execute(stmt)).scalar_one_or_none(); None -> select id, status by fingerprint -> created=False
  async def get_alert(session: AsyncSession, alert_id: uuid.UUID) -> AlertRow: ...        # NotFoundError
  async def set_alert_status(session: AsyncSession, alert_id: uuid.UUID, status: AlertStatus) -> None: ...

  # core/schemas/ingest.py
  class IngestResponse(BaseModel):
      id: uuid.UUID; status: AlertStatus; created: bool

  # api/routes/alerts.py — router = APIRouter(); POST "/alerts", operation_id="ingest_alert", response_model=IngestResponse
  async def ingest_alert(payload: SessionAlert, _sig: None = Depends(require_signature),
                         session: SessionDep, triage: TriageFn = Depends(get_triage)) -> JSONResponse: ...
      # SessionDep (task-02 review ruling I5): scope="function" makes commit/rollback run before the response is built,
      # so a failing commit is the 500 envelope, never a 2xx with the row rolled back.
      # require_signature is declared BEFORE payload parsing takes effect: FastAPI resolves dependencies in declaration order,
      # and require_signature reads the raw body itself, so a bad signature is 401 even when the body is invalid JSON.
      # result = await insert_alert(session, payload); if result.created: await session.commit(); status = await triage(session, result.alert_id)
      # else: status = result.status. Return 202 when created else 200, body IngestResponse(id=..., status=status, created=result.created).

  # scripts/post_alert.py — `uv run python scripts/post_alert.py <fixture.json> [--url http://127.0.0.1:8000]`:
  #   reads INGEST_HMAC_SECRET from the environment only (the README shows the `export` line; no dotenv dependency),
  #   signs with sign_body, POSTs with httpx, prints "<status> <body>"; exit 0 on 2xx, 1 otherwise
  #   exposes main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int
  #   (transport injectable for tests; default = real network) — controller ruling, so the script has a test row
  #   (tests/test_post_alert.py) like every other Interfaces line. No argparse helper copy (M1 defect 8): plain
  #   argparse + sys.exit codes; the shared CLI helper extraction is M5's.
  ```

  Note on ordering: FastAPI evaluates `Depends` parameters before validating the body model
  only if the body-reading dependency is resolved first; the test
  `test_signature_checked_before_body_validation` pins this. If declaration order proves
  insufficient, implement the check as a small ASGI middleware scoped to `POST /api/v1/alerts`
  — the observable contract (401 before 422) is what is pinned, not the mechanism.

## Steps (TDD)

- [ ] **Step 1: Write failing tests.** `tests/test_signing.py`:
  `test_sign_body_is_sha256_prefixed_hex`, `test_verify_accepts_valid`,
  `test_verify_rejects_tampered_body`, `test_verify_rejects_missing_or_malformed_header`,
  `test_verify_rejects_empty_secret`. `tests/test_alert_service.py` (DB):
  `test_insert_conflict_returns_existing_id_and_status`,
  `test_get_alert_missing_raises_not_found`, `test_set_alert_status`.
  `tests/test_ingest.py` (DB; `app = create_app(session_factory=..., settings=settings,
  triage=fake_triage)` where `fake_triage` records calls and returns `"triaged"`; bodies from
  `fixtures/alerts/alert4.json`; signed with `sign_body("test-secret", body)`):
  `test_unsigned_post_401`, `test_bad_signature_401`, `test_signed_post_202_and_inserts_row`,
  `test_duplicate_post_200_same_id_no_new_row`, `test_invalid_payload_422` (signed garbage),
  `test_signature_checked_before_body_validation` (unsigned garbage → 401, not 422),
  `test_duplicate_post_does_not_invoke_triage`.
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: core.signing` etc.
- [ ] **Step 3: Implement signing, service, schema, route, `require_signature`; include the
  router; regenerate `api/openapi.json`.**
- [ ] **Step 4: Implement `scripts/post_alert.py`.**
- [ ] **Step 5: Tests pass (export line), mypy clean, `lint-imports` 5 kept.**
- [ ] **Step 6: Full gates → commit:**
  `feat(api): HMAC-signed ingest with fingerprint dedup (m2 task-03)`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_signing.py tests/test_alert_service.py tests/test_ingest.py     # 15 passed
uv run python scripts/export_openapi.py && git diff --exit-code -- api/openapi.json           # baseline regenerated and committed
```

## Acceptance

- Unsigned → 401; bad signature → 401 (even with an invalid body); signed valid → 202 with an
  id; the same session again → 200, same id, no new row, triage not called; signed invalid →
  422 envelope without the input.
