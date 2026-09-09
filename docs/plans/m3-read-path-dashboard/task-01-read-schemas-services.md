---
id: task-01
milestone: m3-read-path-dashboard
depends_on: []
status: planned
spec: PRD.md §5 (verdicts/tool_calls columns), §8 (list/detail/stats contracts, filters, pagination, envelope), §9 ("sorted by severity desc, then recency"), §12 M3; CONVENTIONS.md §2, §3, §10
---

# task-01 — Read DTOs (`PaginatedResponse[T]`, `ErrorEnvelope`, alert/verdict/stats views), read services with the latest-verdict query and stable tiebreaker, `tests/helpers.py` consolidation

## Goal

The M3 wire contract is frozen by types before any route exists: one generic pagination envelope,
one error envelope, the alert/verdict/tool-call/stats read views, and a `ListFilters` model. Three
session-first read services (`list_alerts`, `get_alert_detail`, `get_stats`) answer PRD §8 from
the database alone — the latest verdict per alert via a `DISTINCT ON` subquery outer-joined so
`pending`/`failed` alerts still list, ordering `severity DESC NULLS LAST, received_at DESC, id DESC`
(the `id` tiebreaker makes pages never drop or duplicate a row), and stats with SQL percentiles.
The four M2 test files that each carry a private copy of `_signed_headers` / `_count_alerts` /
`_load_alert` / `_insert_alert` switch to one `tests/helpers.py` module before a fifth copy appears
(M2 final review, plan defect 14).

## Context (read ONLY these)

- `PRD.md` §5, §8, §9 (page 1 sort order), §12 M3.
- `docs/plans/m3-read-path-dashboard.md` — Global Constraints (pagination envelope, tiebreaker,
  "nothing triggers compute").
- `CONVENTIONS.md` §2 (layering: `core.services` → `core.models core.schemas core.errors`), §3
  (services never commit), §10 (tests; `FakeLLMClient` is the only LLM double).
- `.claude/rules/core.md`, `.claude/rules/tests.md`.
- Code you build on: `core/models/{alerts,verdicts,tool_calls,base}.py`, `core/services/alerts.py`
  (`insert_alert`, `get_alert`, `set_alert_status`), `core/schemas/{alert,verdict,ingest}.py`
  (`SessionAlert`, `Verdict`, `VerdictCategory`, `AlertStatus` via `core.models`), `core/errors.py`
  (`NotFoundError`), `worker/store.py` (`persist_verdict`, `ToolCallRecord`), `worker/triage.py`
  (`TriageOutcome`), `evals/scoring.py` (`percentile` — the nearest-rank definition the SQL
  percentile must agree with), `tests/conftest.py` (`db_session`, `db_session_factory`,
  `settings`), `tests/fakes.py`.
- The four files whose helpers you consolidate: `tests/test_ingest.py`,
  `tests/test_inline_triage.py`, `tests/test_store.py`, `tests/test_alert_service.py`.

## Files

- Create: `core/schemas/pagination.py`, `core/schemas/errors.py`, `core/schemas/alerts_read.py`,
  `core/services/alerts_read.py`
- Create (test-author): `tests/helpers.py`, `tests/test_read_schemas.py`,
  `tests/test_alerts_read_service.py`, `tests/test_helpers.py`
- Modify (test-author — helper switch only, no assertion changes; every file is re-pinned):
  `tests/test_ingest.py`, `tests/test_inline_triage.py`, `tests/test_store.py`,
  `tests/test_alert_service.py`
- Modify: `core/schemas/__init__.py` (re-export the new DTOs)

## Interfaces

- **Consumes:** `AlertRow`, `VerdictRow`, `ToolCallRow`, `Base`, `AlertStatus`, `SessionAlert`,
  `Verdict`, `VerdictCategory`, `NotFoundError`, `core.services.alerts.insert_alert` /
  `get_alert` / `set_alert_status`, `worker.store.persist_verdict` / `ToolCallRecord`,
  `worker.triage.TriageOutcome`, `core.signing.SIGNATURE_HEADER` / `sign_body`, the
  `db_session` / `db_session_factory` fixtures.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/schemas/pagination.py
  class PaginatedResponse[T](BaseModel):
      items: list[T]
      total: int          # rows matching the filters, not the page length
      page: int
      page_size: int
  # Codegen name: FastAPI 0.141 / pydantic 2.13 register the parametrized schema as
  # "PaginatedResponse_AlertSummary_" in api/openapi.json (verified at briefing time from
  # create_app().openapi()["components"]["schemas"]); task-02 pins it, task-03's codegen consumes
  # components["schemas"]["PaginatedResponse_AlertSummary_"].

  # core/schemas/errors.py — the PRD §8 envelope as a model (M2 final review, plan defect 3)
  class ErrorBody(BaseModel):
      code: str
      message: str
  class ErrorEnvelope(BaseModel):
      error: ErrorBody

  # core/schemas/alerts_read.py
  REASONING_EXCERPT_CHARS = 160
  def reasoning_excerpt(reasoning: str) -> str: ...     # reasoning[:REASONING_EXCERPT_CHARS]; plain slice, no ellipsis

  class VerdictOut(BaseModel):                            # every `verdicts` column except alert_id
      model_config = ConfigDict(from_attributes=True)
      id: uuid.UUID; severity: int; category: VerdictCategory; confidence: float; reasoning: str
      recommended_action: str; escalate: bool; model_primary: str; model_final: str
      escalated_model: bool; prompt_version: str; input_tokens: int | None
      output_tokens: int | None; cost_usd: Decimal | None; latency_ms: int | None
      created_at: datetime
      # cost_usd is a JSON **string** ("0.000228"): pydantic v2's default Decimal serialization
      # (OpenAPI `type: string` with the decimal pattern in serialization mode — verified against
      # FastAPI 0.141). PRD §8 is silent on the representation; a string keeps numeric(10,6)
      # lossless on the wire and needs no custom serializer. The web formats it (task-04
      # `formatUsd`). Decision recorded in the briefing report.

  class VerdictSummary(BaseModel):
      severity: int; category: VerdictCategory; confidence: float; escalate: bool
      reasoning_excerpt: str                              # reasoning_excerpt(row.reasoning)
      created_at: datetime

  class ToolCallOut(BaseModel):
      model_config = ConfigDict(from_attributes=True)
      seq: int; tool_name: str; arguments: dict[str, Any]; result: dict[str, Any]
      latency_ms: int | None

  class AlertBase(BaseModel):                             # shared fields; never a response_model itself
      id: uuid.UUID; source: str; src_ip: str; sensor: str
      event_time: datetime; received_at: datetime; status: AlertStatus
  class AlertSummary(AlertBase):
      verdict: VerdictSummary | None                      # None for pending/failed alerts
  class AlertDetail(AlertBase):
      raw: dict[str, Any]                                 # the full session payload (PRD §5)
      verdict: VerdictOut | None                          # latest verdict
      tool_calls: list[ToolCallOut]                       # of the latest verdict, ordered by seq; [] until M4

  class DayVolume(BaseModel):
      day: date; count: int
  class StatsOut(BaseModel):                              # PRD §8: volume by day, severity distribution, mean cost/alert, p95 latency
      total_alerts: int                                   # count(alerts)
      by_status: dict[str, int]                           # keys exactly "pending","triaged","failed", zero-filled
      by_severity: dict[str, int]                         # keys exactly "1".."5", zero-filled; latest verdict per alert
      by_category: dict[str, int]                         # keys exactly the seven VerdictCategory literals (typing.get_args order), zero-filled; latest verdict per alert
      escalated_count: int                                # latest verdicts with escalate = true
      volume_by_day: list[DayVolume]                      # alerts grouped by received_at's UTC date, ascending; only days with >= 1 alert
      cost_total_usd: Decimal                             # sum(cost_usd) over ALL verdict rows (retriage rows included: that spend happened); Decimal("0") when none
      cost_mean_usd: Decimal                              # avg(cost_usd) over verdict rows with a non-null cost, quantized to 6 dp; Decimal("0.000000") when none
      latency_p50_ms: int                                 # percentile_disc(0.5) within group (order by latency_ms) over verdict rows with non-null latency; 0 when none
      latency_p95_ms: int                                 # percentile_disc(0.95), same population; 0 when none
      last_alert_at: datetime | None                      # max(received_at)
      # Percentile choice: Postgres `percentile_disc` — computed in SQL (stats are whole-table
      # aggregates; never stream every verdict through Python) and *identical* in definition to
      # evals/scoring.py::percentile's nearest rank (`sorted[ceil(p*n)-1]`), unlike
      # `percentile_cont`, which interpolates. One definition of "p95" across evals and the API.

  class ListFilters(BaseModel):
      severity_gte: Annotated[int, Field(ge=1, le=5)] | None = None   # latest verdict severity >= value
      category: VerdictCategory | None = None                         # latest verdict category == value
      since: datetime | None = None                                   # received_at >= since; a naive value is treated as UTC (field_validator sets tzinfo=UTC)
      escalate: bool | None = None                                    # latest verdict escalate == value
      # A verdict-field filter naturally excludes alerts without a verdict (NULL comparisons).

  # core/services/alerts_read.py — async, session-first, read-only, never commit()
  async def list_alerts(session: AsyncSession, *, filters: ListFilters, page: int, page_size: int) -> tuple[list[AlertSummary], int]: ...
      # latest = (select(VerdictRow).distinct(VerdictRow.alert_id)
      #             .order_by(VerdictRow.alert_id, VerdictRow.created_at.desc(), VerdictRow.id.desc())
      #             .subquery("latest"))            # Postgres DISTINCT ON (alert_id); id DESC breaks a same-transaction created_at tie
      # base = (select(AlertRow.id, AlertRow.source,
      #                func.coalesce(AlertRow.raw["src_ip"].astext, "").label("src_ip"),
      #                func.coalesce(AlertRow.raw["sensor"].astext, "").label("sensor"),
      #                AlertRow.event_time, AlertRow.received_at, AlertRow.status,
      #                latest.c.severity, latest.c.category, latest.c.confidence, latest.c.escalate,
      #                latest.c.reasoning, latest.c.created_at)
      #         .outerjoin(latest, latest.c.alert_id == AlertRow.id))
      #   — src_ip/sensor via `->>` in SQL: the list never loads whole `raw` JSONB payloads
      # filters (applied to `base`): severity_gte -> latest.c.severity >= v; category -> latest.c.category == v;
      #   escalate -> latest.c.escalate == v; since -> AlertRow.received_at >= since
      # total = select(func.count()).select_from(base.subquery())
      # items = base.order_by(latest.c.severity.desc().nulls_last(), AlertRow.received_at.desc(), AlertRow.id.desc())
      #             .offset((page - 1) * page_size).limit(page_size)
      # verdict=None when latest.c.severity is NULL, else VerdictSummary(..., reasoning_excerpt=reasoning_excerpt(reasoning))

  async def get_alert_detail(session: AsyncSession, alert_id: uuid.UUID) -> AlertDetail: ...
      # row = await core.services.alerts.get_alert(session, alert_id)      # NotFoundError propagates
      # latest = select(VerdictRow).where(alert_id == ...).order_by(created_at.desc(), id.desc()).limit(1)
      # tool_calls = select(ToolCallRow).where(verdict_id == latest.id).order_by(ToolCallRow.seq)   # [] when no verdict
      # src_ip = row.raw.get("src_ip", ""); sensor = row.raw.get("sensor", ""); raw = row.raw

  async def get_stats(session: AsyncSession) -> StatsOut: ...
      # same `latest` subquery for by_severity / by_category / escalated_count;
      # by_status over AlertRow; volume_by_day = group by cast(func.timezone("UTC", AlertRow.received_at), Date), ascending;
      # cost_total_usd = coalesce(sum(VerdictRow.cost_usd), 0); cost_mean_usd = avg(VerdictRow.cost_usd) quantized 6 dp;
      # latency_pNN_ms = func.percentile_disc(p).within_group(VerdictRow.latency_ms); last_alert_at = max(AlertRow.received_at)

  # tests/helpers.py — imported as `tests.helpers` (tests/ has no __init__.py; `tests.fakes` already imports this way)
  FIXTURES_DIR: Path = <repo>/fixtures/alerts
  TEST_SECRET = "test-secret"                                       # matches conftest's `settings` fixture
  def fixture_body(name: str = "alert4") -> bytes: ...             # (FIXTURES_DIR / f"{name}.json").read_bytes()
  def load_alert(name: str = "alert4", **overrides: object) -> SessionAlert: ...   # json.loads + data.update(overrides) + model_validate
  def signed_headers(secret: str, body: bytes) -> dict[str, str]: ...  # {SIGNATURE_HEADER: sign_body(secret, body), "content-type": "application/json"}
  async def count_rows(session: AsyncSession, model: type[Base]) -> int: ...
  async def count_rows_fresh(session_factory: async_sessionmaker[AsyncSession], model: type[Base]) -> int: ...  # opens its own session (visibility from a fresh connection)
  async def add_verdict(session: AsyncSession, alert_id: uuid.UUID, verdict: Verdict, *,
                        created_at: datetime | None = None, cost_usd: Decimal = Decimal("0.000100"),
                        latency_ms: int = 5, tool_calls: Sequence[ToolCallRecord] = ()) -> uuid.UUID: ...
      # persist_verdict(session, alert_id=alert_id, outcome=TriageOutcome(verdict=verdict, model="fake-model",
      #   prompt_version="triage-v1", input_tokens=100, output_tokens=50, cost_usd=cost_usd, latency_ms=latency_ms,
      #   retried=False), model_primary="fake-model", tool_calls=tool_calls); when created_at is given, set
      #   VerdictRow.created_at afterwards and flush (two verdicts written in one transaction share now()).
  async def seed_alert(session: AsyncSession, name: str = "alert4", *, verdict: Verdict | None = None,
                       status: AlertStatus | None = None, session_id: str | None = None,
                       received_at: datetime | None = None, verdict_created_at: datetime | None = None,
                       cost_usd: Decimal = Decimal("0.000100"), latency_ms: int = 5,
                       tool_calls: Sequence[ToolCallRecord] = ()) -> uuid.UUID: ...
      # 1. alert = load_alert(name, session_id=session_id or uuid.uuid4().hex[:12])  (fresh fingerprint per call)
      # 2. result = await insert_alert(session, alert); assert result.created (a duplicate is a test bug)
      # 3. received_at given -> AlertRow.received_at = received_at; flush
      # 4. verdict given -> add_verdict(session, result.alert_id, verdict, created_at=verdict_created_at, cost_usd=..., latency_ms=..., tool_calls=...)
      # 5. effective = status if status is not None else ("triaged" if verdict else "pending");
      #    set_alert_status when it differs from the row's current status
      # 6. flush; return result.alert_id     — the caller commits (services/helpers never commit)
  ```

  Shapes go through the production writers (`insert_alert`, `persist_verdict`,
  `set_alert_status`) so seeded rows are byte-for-byte what ingest + triage produce.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `PaginatedResponse[T]` generic | `tests/test_read_schemas.py::test_paginated_response_is_generic_over_item_type` | `PaginatedResponse[int]` and `PaginatedResponse[AlertSummary]` both validate; missing `total` is a `ValidationError` |
| `ErrorBody` / `ErrorEnvelope` | `tests/test_read_schemas.py::test_error_envelope_matches_prd_shape` | `model_dump()` is exactly `{"error": {"code", "message"}}` |
| `VerdictOut` column coverage | `tests/test_read_schemas.py::test_verdict_out_covers_every_verdict_column_except_alert_id` | `set(VerdictOut.model_fields) == {c.name for c in VerdictRow.__table__.columns} - {"alert_id"}` |
| `VerdictOut.cost_usd` string | `tests/test_read_schemas.py::test_verdict_out_serializes_cost_usd_as_decimal_string` | `model_dump(mode="json")["cost_usd"] == "0.000228"`; `None` stays `null` |
| `reasoning_excerpt` | `tests/test_read_schemas.py::test_reasoning_excerpt_is_first_160_chars` | 200-char input → 160 chars, no suffix; 10-char input unchanged |
| `ListFilters` validators | `tests/test_read_schemas.py::test_list_filters_reject_out_of_range_severity_and_unknown_category` | `severity_gte=0`, `severity_gte=6`, `category="bogus"` each raise `ValidationError` |
| `ListFilters.since` naive → UTC | `tests/test_read_schemas.py::test_list_filters_since_naive_is_treated_as_utc` | naive input gains `tzinfo=UTC`; aware input unchanged |
| `AlertSummary` / `AlertDetail` share `AlertBase` | `tests/test_read_schemas.py::test_alert_detail_and_summary_share_base_fields` | field sets differ by exactly `{raw, tool_calls}` and the `verdict` type |
| `list_alerts` ordering | `tests/test_alerts_read_service.py::test_list_alerts_orders_severity_desc_then_received_at_desc` | sev 4 (T+1m), sev 4 (T), sev 2 (T+2m) → that order |
| `list_alerts` NULLS LAST | `tests/test_alerts_read_service.py::test_list_alerts_lists_pending_and_failed_with_null_verdict_last` | triaged sev 1 first; pending/failed after, `verdict is None`, by received_at desc |
| `list_alerts` latest verdict | `tests/test_alerts_read_service.py::test_list_alerts_uses_latest_verdict_per_alert` | two verdicts (sev 2 at T, sev 5 at T+1h) → one item, severity 5, `total == 1` |
| `list_alerts` id tiebreaker | `tests/test_alerts_read_service.py::test_list_alerts_pagination_tiebreaker_never_drops_or_duplicates` | 7 rows, identical severity and received_at, page_size 3 → pages of 3/3/1, all ids distinct, union == all 7, `total == 7` on every page |
| `severity_gte` filter | `tests/test_alerts_read_service.py::test_list_alerts_filter_severity_gte` | sev 1/3/5 + pending → `severity_gte=3` → [5, 3], pending excluded |
| `category` filter | `tests/test_alerts_read_service.py::test_list_alerts_filter_category` | scanning + 2× brute_force → `category="brute_force"` → 2 |
| `escalate` filter | `tests/test_alerts_read_service.py::test_list_alerts_filter_escalate` | `True` → only the escalated; `False` → only the non-escalated; pending in neither |
| `since` filter | `tests/test_alerts_read_service.py::test_list_alerts_filter_since_on_received_at` | received T and T+1h; `since=T+30m` → only the newer |
| `total` semantics | `tests/test_alerts_read_service.py::test_list_alerts_total_counts_filtered_rows_not_page` | 5 matching rows, page_size 2 → `len(items) == 2`, `total == 5` |
| src_ip/sensor from `raw` | `tests/test_alerts_read_service.py::test_list_alerts_src_ip_and_sensor_come_from_raw` | equal to `load_alert("alert4").src_ip` / `.sensor` |
| `get_alert_detail` happy path | `tests/test_alerts_read_service.py::test_get_alert_detail_returns_latest_verdict_and_tool_calls_in_seq_order` | older + newer verdict → newer returned; tool calls inserted seq 1 then 0 → returned [0, 1] |
| `get_alert_detail` no verdict | `tests/test_alerts_read_service.py::test_get_alert_detail_pending_alert_has_null_verdict_and_no_tool_calls` | `verdict is None`, `tool_calls == []`, `status == "pending"` |
| `get_alert_detail` NotFoundError | `tests/test_alerts_read_service.py::test_get_alert_detail_missing_raises_not_found` | `uuid4()` → `NotFoundError` |
| `AlertDetail.raw` | `tests/test_alerts_read_service.py::test_get_alert_detail_includes_raw_payload` | `raw == load_alert(..., session_id=...).model_dump(mode="json")` |
| `get_stats` empty | `tests/test_alerts_read_service.py::test_get_stats_empty_db_is_zero_filled` | every key present and 0 / `[]` / `Decimal("0")` / `None` |
| `get_stats` latest-verdict distributions | `tests/test_alerts_read_service.py::test_get_stats_distributions_use_latest_verdict_per_alert` | alert with sev 2 then sev 5 (escalate) + alert sev 1 → `by_severity == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 1}`, `escalated_count == 1`, `cost_total_usd == Decimal("0.000300")` (all three verdict rows) |
| `get_stats` cost + percentiles | `tests/test_alerts_read_service.py::test_get_stats_cost_totals_and_latency_percentiles` | latencies [10, 20, 30, 40, 100] at cost 0.000100 each + one pending → p50 30, p95 100, total 0.000500, mean 0.000100 |
| `get_stats.volume_by_day` | `tests/test_alerts_read_service.py::test_get_stats_volume_by_day_groups_received_at_by_utc_date` | 2026-09-01T23:30Z, 2026-09-01T00:10Z, 2026-09-02T12:00Z → `[(2026-09-01, 2), (2026-09-02, 1)]` |
| `get_stats.last_alert_at` | `tests/test_alerts_read_service.py::test_get_stats_last_alert_at_is_max_received_at` | equals the latest `received_at` override |
| `fixture_body` | `tests/test_helpers.py::test_fixture_body_returns_raw_file_bytes` | bytes equal `Path(...).read_bytes()` |
| `load_alert` | `tests/test_helpers.py::test_load_alert_applies_overrides` | `session_id` override changes `fingerprint()` |
| `signed_headers` | `tests/test_helpers.py::test_signed_headers_verify_against_the_body` | `verify_signature(secret, body, headers[SIGNATURE_HEADER]) is True`; wrong body → False |
| `count_rows` / `count_rows_fresh` | `tests/test_helpers.py::test_count_rows_counts_the_given_model` | 2 alerts + 1 verdict → 2 / 1; fresh count sees only committed rows |
| `seed_alert` with verdict | `tests/test_helpers.py::test_seed_alert_with_verdict_goes_through_production_writers` | status `triaged`, one `VerdictRow` with `model_primary == "fake-model"`, tool-call rows present when given |
| `seed_alert` without verdict | `tests/test_helpers.py::test_seed_alert_without_verdict_is_pending` | status `pending`, zero verdicts |
| `seed_alert` status override | `tests/test_helpers.py::test_seed_alert_status_override` | `status="failed"` with no verdict → `failed`; `status="failed"` with a verdict → `failed` and the verdict row still exists |
| `seed_alert` unique fingerprint | `tests/test_helpers.py::test_seed_alert_mints_a_unique_fingerprint_per_call` | two calls with the same `name` → two rows |
| `seed_alert` time overrides / `add_verdict` | `tests/test_helpers.py::test_seed_alert_received_at_and_verdict_created_at_overrides` | `received_at` and `verdict_created_at` land on the rows; `add_verdict(created_at=...)` on the same alert yields a second row with that timestamp |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 (including `tests/helpers.py` and the helper switch in
the four M2 files) and pins every file it touched with a sha256; the **implementer** does Steps 3–5
and never edits a pinned file — it stops and asks the controller if a pinned test looks wrong.

- [ ] **Step 1 (RED — test-author): write `tests/helpers.py` and the three new test files** exactly
  per Interfaces and the table above. Then switch the four M2 files to the helpers, assertion for
  assertion unchanged: `tests/test_ingest.py` (`_signed_headers(body)` → `signed_headers(TEST_SECRET,
  body)`, `_FIXTURE_BODY` → `fixture_body("alert4")`, `_count_alerts(factory)` →
  `count_rows_fresh(factory, AlertRow)`), `tests/test_inline_triage.py` (same plus `_load_alert` →
  `load_alert`, `_count_verdicts(factory)` → `count_rows_fresh(factory, VerdictRow)`),
  `tests/test_store.py` (`_insert_alert(session, session_id=...)` → `seed_alert(db_session,
  session_id=...)`; `_make_outcome` stays — it is not one of the four consolidated helpers),
  `tests/test_alert_service.py` (`_load_alert` → `load_alert`, `_count_alerts(session)` →
  `count_rows(session, AlertRow)`). Delete the private copies. Run `uv run ruff format` and `uv run
  ruff check` on every touched file.
- [ ] **Step 2 (RED — test-author): run to see them fail, and the refactor stay green.**
  `uv run pytest -q tests/test_read_schemas.py tests/test_alerts_read_service.py` → Expected:
  `ModuleNotFoundError: No module named 'core.schemas.pagination'` (collection error, every test
  in both files). `uv run pytest -q tests/test_helpers.py` → the DB-less helper tests pass and the
  `seed_alert` tests pass (helpers depend only on M2 code) — record that they are green on
  arrival. `uv run pytest -q tests/test_ingest.py tests/test_inline_triage.py tests/test_store.py
  tests/test_alert_service.py` → `28 passed` (the helper switch is behavior-preserving). Pin all
  eight files (`sha256sum`), commit `test(core): read DTO/service tests, tests.helpers
  consolidation RED (m3 task-01)`.
- [ ] **Step 3 (GREEN — implementer): implement `core/schemas/pagination.py`,
  `core/schemas/errors.py`, `core/schemas/alerts_read.py`** per Interfaces; re-export
  `PaginatedResponse`, `ErrorBody`, `ErrorEnvelope`, `AlertSummary`, `AlertDetail`, `VerdictOut`,
  `VerdictSummary`, `ToolCallOut`, `StatsOut`, `DayVolume`, `ListFilters` from
  `core/schemas/__init__.py`. `uv run mypy` clean before moving on.
- [ ] **Step 4 (GREEN — implementer): implement `core/services/alerts_read.py`** per the SQL shapes
  in Interfaces. Docstrings cite PRD §8/§9 and the tiebreaker rationale. `uv run mypy` clean.
- [ ] **Step 5 (implementer): tests pass with the export line set; `uv run lint-imports` still
  reports 5 kept contracts; full gates → commit:**
  `feat(core): read DTOs, latest-verdict read services, tests.helpers consolidation (m3 task-01)`
  with the two trailers from `CONVENTIONS.md` §12. Path-scoped `git add` only.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_read_schemas.py tests/test_alerts_read_service.py tests/test_helpers.py   # 36 passed
uv run pytest -q tests/test_ingest.py tests/test_inline_triage.py tests/test_store.py tests/test_alert_service.py   # 28 passed
grep -rnE "^(async )?def _(signed_headers|count_alerts|count_verdicts|load_alert|insert_alert)\b" tests/   # no output — no private helper definition remains
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean; "Contracts: 5 kept, 0 broken"
```

## Acceptance

- `list_alerts` returns the latest verdict per alert, lists `pending`/`failed` alerts with
  `verdict=None` after every verdict-bearing row, honors all four PRD §8 filters, and pages with
  a deterministic `id` tiebreaker (the 7-row identical-key test never drops or duplicates a row).
- `get_alert_detail` returns raw payload + latest verdict + tool calls in `seq` order, and raises
  `NotFoundError` for an unknown id; `get_stats` is zero-filled on an empty database and its
  `p50`/`p95` agree with `evals/scoring.py::percentile`'s nearest-rank definition.
- Exactly one copy of each test helper exists, in `tests/helpers.py`; the four M2 files import it
  and keep every assertion.
