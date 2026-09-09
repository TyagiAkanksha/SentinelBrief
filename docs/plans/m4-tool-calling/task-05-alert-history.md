---
id: task-05
milestone: m4-tool-calling
depends_on: [task-01]
status: planned
spec: PRD.md §6.3 (`get_alert_history(ip, window_hours) -> {count, first_seen, categories}`: SQL over `alerts` via the expression index on `raw->>'src_ip'`, joined to each alert's latest verdict), §5 (`ix_alerts_src_ip`), §6.2 (one transaction per verdict — the history read must never poison it); CONVENTIONS.md §2 (`core.services` → `core.models core.schemas`; the only DB-touching tool goes through `core/services/`), §3 (services flush, never commit), §7 (bounds are Settings); `.claude/rules/{core,worker,tests}.md`
---

# task-05 — `get_alert_history`: `core/services/alert_history.py` (SQL over the `src_ip` expression index + latest verdict per alert) and the `AlertHistoryTool` (window clamp, savepoint-isolated, unavailable without a session)

## Goal

The only tool that touches the database, split the way CONVENTIONS §2 demands: a session-first,
read-only service `core.services.alert_history.get_alert_history(session, *, src_ip, since,
exclude_fingerprint)` that counts the *other* sessions from the same source address received in
the window, their earliest `received_at`, and the distribution of their latest-verdict categories
(the M3 `DISTINCT ON` subquery, made public as `latest_verdicts_subquery`); and
`AlertHistoryTool` in `worker/tools/`, which validates the address, clamps `window_hours` to
`[1, ALERT_HISTORY_MAX_WINDOW_HOURS]`, excludes the session being triaged by fingerprint, runs the
service inside a SAVEPOINT (`session.begin_nested()`) so a failed statement can never abort the
triage transaction that `persist_verdict` needs afterwards, and answers
`unavailable("no_database")` when `ToolContext.session` is `None` (the CLI and evals). Synthetic
history fixtures for the five fixture IPs land under `tests/fixtures/tools/get_alert_history/`.

## Context (read ONLY these)

- `PRD.md` §5, §6.2, §6.3 (history row).
- `docs/plans/m4-tool-calling.md` — Global Constraints (`get_alert_history` is the only tool
  touching the DB and does so through `core/services/`; tool failures never raise).
- `CONVENTIONS.md` §2, §3, §4, §7, §10; `.claude/rules/core.md`, `.claude/rules/worker.md`,
  `.claude/rules/tests.md`.
- Task-01 outputs: `worker/tools/base.py` (`ToolContext.session`, `ToolContext.now`),
  `worker/tools/recorder.py` (`write_fixture`, `ReplayToolRecorder`).
- Code you build on: `core/models/alerts.py` (`ix_alerts_src_ip` on `(raw ->> 'src_ip')` —
  `AlertRow.raw["src_ip"].astext` renders exactly that expression), `core/services/alerts_read.py`
  (`_latest_verdicts_subquery` — becomes public here), `core/schemas/alert.py`
  (`SessionAlert.fingerprint()`), `core/config.py`, `.env.example`, `tests/helpers.py`
  (`seed_alert(received_at=…, verdict=…)`, `load_alert`), `tests/test_alerts_read_service.py`
  (the seeding style to copy), `tests/conftest.py` (`db_session`).
- SQLAlchemy 2 async: `async with session.begin_nested():` issues `SAVEPOINT`; an exception
  inside rolls back to it and the outer transaction stays usable.

## Files

- Create: `core/services/alert_history.py`, `worker/tools/alert_history.py`,
  `tests/fixtures/tools/get_alert_history/6b221b824dde73ee.json` (203.0.113.10, 24 h),
  `…/f2c62396cb97436e.json` (198.51.100.23), `…/13c21d70007c65cc.json` (203.0.113.77),
  `…/93d69809f666ddfa.json` (192.0.2.55), `…/29304a7ba7d6c4f6.json` (198.51.100.140)
- Create (test-author): `tests/test_alert_history_service.py`, `tests/test_alert_history_tool.py`
- Modify: `core/services/alerts_read.py` (rename `_latest_verdicts_subquery` →
  `latest_verdicts_subquery`, its three internal call sites updated; nothing else),
  `core/config.py`, `.env.example`, `worker/tools/__init__.py` (`core/services/__init__.py`
  re-exports nothing today and stays untouched)

## Interfaces

- **Consumes:** `AlertRow`, `VerdictRow` (`core.models`); `latest_verdicts_subquery`
  (`core.services.alerts_read`, made public here); `Tool`, `ToolContext`, `unavailable`;
  `write_fixture`, `ReplayToolRecorder`; `Settings`; `seed_alert`, `load_alert`
  (`tests.helpers`); `db_session`.
- **Produces (task-06 wiring relies on — produce exactly):**

  ```python
  # core/services/alerts_read.py — one rename, no behavior change
  def latest_verdicts_subquery() -> Subquery: ...          # was _latest_verdicts_subquery; same body, same docstring + "shared with alert_history"

  # core/services/alert_history.py — async, session-first, read-only, never commit()
  @dataclass(frozen=True)
  class AlertHistory:
      count: int                                            # alerts from src_ip with received_at >= since (minus the excluded fingerprint)
      first_seen: datetime | None                           # min(received_at) over those, None when count == 0
      categories: dict[str, int]                            # latest-verdict category -> count over those; only categories present (no zero-fill);
                                                            #   pending/failed alerts (no verdict) count in `count` but not here
  async def get_alert_history(session: AsyncSession, *, src_ip: str, since: datetime,
                              exclude_fingerprint: str | None = None) -> AlertHistory: ...
      # Preconditions (stated per plan defect 6): `since` must be timezone-aware — a naive datetime raises ValueError before any SQL;
      #   `src_ip` is passed through as an opaque string (the tool validates it; the service compares equality only, never interpolates)
      # base = select(AlertRow.id, AlertRow.received_at).where(AlertRow.raw["src_ip"].astext == src_ip, AlertRow.received_at >= since)
      #        [.where(AlertRow.fingerprint != exclude_fingerprint)]
      #   — `raw["src_ip"].astext` renders `(alerts.raw ->> 'src_ip')`, the exact expression `ix_alerts_src_ip` indexes; never load `raw`
      # count / first_seen: select(func.count(), func.min(AlertRow.received_at)) over the same predicates          (1 statement)
      # categories: latest = latest_verdicts_subquery(); select(latest.c.category, func.count()).select_from(AlertRow)
      #   .join(latest, latest.c.alert_id == AlertRow.id).where(<same predicates>).group_by(latest.c.category)             (1 statement)
      # 2 statements total; flush nothing; commit nothing

  # worker/tools/alert_history.py
  class AlertHistoryTool:
      name = "get_alert_history"
      external = True                                       # depends on the alerts table -> replayed from fixtures in evals
      description = ("Count earlier sessions from the same source IP in the last N hours, when the first was seen, and how they were "
                     "categorised. Use it to tell a first-time visitor from a persistent attacker.")
      parameters = {"type": "object",
                    "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."},
                                   "window_hours": {"type": "integer", "minimum": 1, "description": "Look-back window in hours; clamped to the configured maximum."}},
                    "required": ["ip", "window_hours"], "additionalProperties": False}
      def __init__(self, *, max_window_hours: int) -> None: ...          # < 1 -> ValueError
      async def run(self, arguments, ctx) -> dict[str, Any]: ...
          # ip missing / not str / ipaddress.ip_address raises      -> unavailable("invalid_arguments")
          # window_hours missing, bool, non-int, or < 1              -> unavailable("invalid_arguments")   (bool is excluded explicitly: True is an int)
          # window_hours > max_window_hours                          -> clamped (effective value reported)
          # ctx.session is None                                      -> unavailable("no_database")
          # since = ctx.now - timedelta(hours=w)
          # try: async with ctx.session.begin_nested(): history = await get_alert_history(ctx.session, src_ip=ip, since=since,
          #                                                        exclude_fingerprint=ctx.alert.fingerprint())
          # except SQLAlchemyError: return unavailable("database_error")   — the SAVEPOINT rolled back; the triage transaction is intact
          # -> {"ip": ip, "window_hours": w, "count": history.count, "first_seen": history.first_seen.isoformat() | None, "categories": history.categories}

  # core/config.py — new field (+ .env.example line under "Enrichment tools (from M4)")
  alert_history_max_window_hours: Annotated[int, Field(ge=1)] = 720       # ALERT_HISTORY_MAX_WINDOW_HOURS=720  (30 days; bounds the scan the model can request)

  # tests/fixtures/tools/get_alert_history/<key>.json — synthetic, arguments {"ip": <ip>, "window_hours": 24}, per fixtures/alerts:
  #   203.0.113.10   -> {"ip": ..., "window_hours": 24, "count": 3,  "first_seen": "2026-09-05T14:02:11+00:00", "categories": {"scanning": 3}}
  #   198.51.100.23  -> {..., "count": 12, "first_seen": "2026-09-05T09:30:00+00:00", "categories": {"brute_force": 12}}
  #   203.0.113.77   -> {..., "count": 0,  "first_seen": null, "categories": {}}
  #   192.0.2.55     -> {..., "count": 1,  "first_seen": "2026-09-06T06:45:27+00:00", "categories": {"brute_force": 1}}
  #   198.51.100.140 -> {..., "count": 2,  "first_seen": "2026-09-06T02:11:48+00:00", "categories": {"brute_force": 1, "malware_delivery": 1}}
  ```

## Interfaces → test table

Service tests seed through `tests.helpers.seed_alert(..., received_at=…, verdict=…)` and pass
`load_alert(name, src_ip=…)` overrides via `session_id`/`received_at` only — `seed_alert` takes a
fixture `name`; to vary `src_ip` the test-author adds a `src_ip: str | None = None` keyword to
`seed_alert` and passes it into its existing `load_alert(name, session_id=…)` call as
`src_ip=src_ip` when not `None` (`load_alert` already applies top-level overrides), then re-pins
`tests/helpers.py`. `T = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)`.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `latest_verdicts_subquery` public | `tests/test_alert_history_service.py::test_latest_verdicts_subquery_is_public_and_alerts_read_still_uses_it` | `from core.services.alerts_read import latest_verdicts_subquery` works; `_latest_verdicts_subquery` no longer exists; `list_alerts` on two verdicts for one alert still returns the newest (behavior unchanged) |
| count + first_seen in window | `tests/test_alert_history_service.py::test_counts_alerts_from_the_ip_in_the_window_and_reports_first_seen` | three alerts from `203.0.113.10` at `T-1h`, `T-3h`, `T-30h` and one from another IP at `T-1h`; `since=T-24h` → `count == 2`, `first_seen == T-3h`; fails when the other IP is counted or the window bound is `>` instead of `>=` (seed one exactly at `since`) |
| categories over latest verdicts | `tests/test_alert_history_service.py::test_categories_use_the_latest_verdict_per_alert_and_skip_verdictless_alerts` | alert A: verdicts `scanning` (T-2h) then `brute_force` (T-1h); alert B: `brute_force`; alert C: pending → `count == 3`, `categories == {"brute_force": 2}`; fails when both of A's verdicts are counted or when C adds a `None` key |
| exclusion by fingerprint | `tests/test_alert_history_service.py::test_exclude_fingerprint_removes_the_current_session` | two alerts from the IP; `exclude_fingerprint=` one of them → `count == 1`; `None` → `2` |
| empty | `tests/test_alert_history_service.py::test_no_history_is_zero_none_empty` | unknown IP → `AlertHistory(count=0, first_seen=None, categories={})` |
| naive `since` | `tests/test_alert_history_service.py::test_naive_since_raises_value_error_before_sql` | `since=datetime(2026, 9, 6)` → `ValueError`; a `before_cursor_execute` listener on the engine captured zero statements |
| index expression, no `raw` load | `tests/test_alert_history_service.py::test_statements_use_the_src_ip_expression_and_never_select_raw` | listener-captured SQL contains `alerts.raw ->> 'src_ip'` and no bare `alerts.raw` (reuse `tests/test_alerts_read_service.py`'s `_BARE_RAW_COLUMN` regex); exactly two statements; fails when a third statement or a whole-`raw` projection appears |
| never commits | `tests/test_alert_history_service.py::test_service_never_commits` | listener sees no `COMMIT`; `session.in_transaction()` still true afterwards |
| tool happy path | `tests/test_alert_history_tool.py::test_returns_history_for_the_context_ip_excluding_the_current_session` | seed the context alert (`alert4`, `192.0.2.55`) + two earlier ones from the same IP; `ctx = ToolContext(alert, db_session, now=T)`; `{"ip": "192.0.2.55", "window_hours": 24}` → `count == 2`, `window_hours == 24`, `first_seen` ISO string with `+00:00`, `categories` dict; fails when the context session itself is counted (3) |
| clamp | `tests/test_alert_history_tool.py::test_window_hours_above_max_is_clamped_and_reported` | `max_window_hours=48`, `window_hours=5000` → `result["window_hours"] == 48` and the seeded alert at `T-60h` is not counted; fails when the raw value is used |
| invalid arguments | `tests/test_alert_history_tool.py::test_invalid_ip_or_window_is_invalid_arguments` | `window_hours` of `0`, `"24"`, `True`, missing; `ip` of `"x"` → `unavailable("invalid_arguments")`, no statement executed |
| no session | `tests/test_alert_history_tool.py::test_no_session_is_unavailable_no_database` | `ToolContext(alert, None, now)` → `unavailable("no_database")` (DB-less test) |
| DB error isolated by savepoint | `tests/test_alert_history_tool.py::test_database_error_is_unavailable_and_the_transaction_survives` | in the throwaway schema run `DROP TABLE verdicts CASCADE` through `db_session` first (a real DB error: the categories join now fails) → `unavailable("database_error")`; then `await db_session.execute(text("SELECT 1"))` succeeds (the SAVEPOINT rolled back, the transaction is not aborted); fails when `InFailedSqlTransaction` surfaces or the tool raises |
| constructor bound | `tests/test_alert_history_tool.py::test_rejects_nonpositive_max_window` | `max_window_hours=0` → `ValueError` |
| `external` | `tests/test_alert_history_tool.py::test_tool_is_external` | `external is True` |
| setting | `tests/test_alert_history_tool.py::test_max_window_setting_default_and_bound` | default `720`; `Settings(alert_history_max_window_hours=0)` → `ValidationError`; roster green |
| fixtures replay | `tests/test_alert_history_tool.py::test_recorded_fixtures_replay_for_the_five_fixture_ips` | `ReplayToolRecorder(Path("tests/fixtures/tools"))` with `{"ip": <ip>, "window_hours": 24}` → the counts `3, 12, 0, 1, 2`; file names equal `fixture_key(arguments)` |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the two new files plus the re-pinned
`tests/helpers.py`; the **implementer** does Steps 3–5 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the two test files** per the table and add the `src_ip`
  override to `tests/helpers.py::seed_alert` (forwarded into `load_alert(name, session_id=…,
  src_ip=…)`; the fixture's events keep their own `src_ip` — only the envelope field the service
  reads changes, which is what `raw->>'src_ip'` indexes).
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_alert_history_service.py tests/test_alert_history_tool.py` → Expected:
  `ModuleNotFoundError: No module named 'core.services.alert_history'` /
  `'worker.tools.alert_history'`; `uv run pytest -q tests/test_helpers.py
  tests/test_alerts_read_service.py` stays green (the helper change is additive). Pin, commit
  `test(core,worker): alert history service + tool RED (m4 task-05)`.
- [ ] **Step 3 (GREEN — implementer): rename `latest_verdicts_subquery` in
  `core/services/alerts_read.py`; `core/services/alert_history.py`; `core/config.py` field +
  `.env.example` line.** `uv run mypy` clean; `uv run pytest -q tests/test_alerts_read_service.py
  tests/test_read_routes.py` green (rename is behavior-preserving).
- [ ] **Step 4 (GREEN — implementer): `worker/tools/alert_history.py` + re-export; the five fixture
  files via `write_fixture`** (paste the loop and `ls` into the report). `uv run mypy` clean.
- [ ] **Step 5 (implementer): all tests in the table + the existing suite green; `uv run
  lint-imports` → 5 kept (the service imports only `core.models`/`core.services.alerts_read`);
  full gates → commit:**
  `feat(core,worker): get_alert_history service over ix_alerts_src_ip + AlertHistoryTool, history fixtures (m4 task-05)`
  with the two trailers; path-scoped `git add core/services worker/tools core/config.py
  .env.example tests/fixtures/tools/get_alert_history`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_alert_history_service.py tests/test_alert_history_tool.py tests/test_helpers.py tests/test_alerts_read_service.py tests/test_env_example_roster.py   # every test in the table passes
grep -n "_latest_verdicts_subquery" core/ ; echo "exit=$?"                              # exit=1 — the private name is gone
ls tests/fixtures/tools/get_alert_history | wc -l                                       # 5
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean; "Contracts: 5 kept, 0 broken"
```

## Acceptance

- `get_alert_history` (service) counts the source address's other sessions in the window through
  the `raw ->> 'src_ip'` expression, reports the earliest `received_at` and the latest-verdict
  category distribution in two statements, never loads `raw`, never commits, and refuses a naive
  `since`.
- `AlertHistoryTool` validates and clamps its arguments, excludes the session being triaged,
  runs under a SAVEPOINT so a database error becomes `unavailable("database_error")` while the
  triage transaction survives, and is `unavailable("no_database")` without a session; five
  synthetic fixtures replay for the fixture alerts' IPs.
