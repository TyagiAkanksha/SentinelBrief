---
id: task-06
milestone: m6-real-data-deploy
depends_on: [task-05]
status: planned
spec: PRD.md §12 M6 (*Accept:* "48 h of real attacker traffic visible publicly; no LLM call originates from any public request (verified in logs); backups and log rotation observed working"), §1.2/§6.1 (the alert unit — the real-session schema check), §7.1 (golden v2 comes from REAL traffic at M7 — this task's schema report is its precondition), §10.1; `docs/plans/m6-real-data-deploy.md` Acceptance walk table + Global Constraint "Synthetic fixture shapes are re-checked against the first real sessions; any schema surprise is a PRD/SUGGESTIONS note, not a silent fixture edit"; `docs/deployment.md` Verification; `infra/deploy/VERIFY.md` (task-05); `infra/deploy/database.md` retention table (task-04); `.claude/rules/evals.md` (v2 labels are human work — this task labels NOTHING)
---

# task-06 — `scripts/check_real_sessions.py` (fixture-vs-real schema report), the 48 h soak, `VERIFY.md` executed with real output, the acceptance walk, and the milestone housekeeping rows

## Goal

The agent-authored half is a small report script the owner runs on the box in a one-off api
container after the first hours of traffic: it reads the newest N alerts, validates each `raw`
payload through the api's own `SessionAlert`, and prints a markdown report of eventid frequencies,
per-eventid field names the synthetic fixtures never used (`CowrieEvent`'s `extra="allow"` keeps
them), sessions the shipper truncated, unclosed (idle-flushed) sessions, duration and event-count
percentiles, and the storage numbers `database.md`'s retention table needs — never an attacker
string (field NAMES and counts only), never a label. The controller/owner half is the soak: 48
hours of real traffic through the deployed stack, `VERIFY.md` executed top to bottom with output
pasted, the report's findings recorded as PRD/SUGGESTIONS notes (a fixture is never edited to
match reality silently), `docs/deployment.md`'s resource table and `database.md`'s retention
numbers filled, and the PRD §12 M6 acceptance walk pasted into the ledger. The milestone
housekeeping rows (rule 15) close it: README status, plans registry, spine Status, ledger
`complete` line.

## Context (read ONLY these)

- `PRD.md` §1.2, §6.1, §7.1, §10.1, §12 M6.
- `docs/plans/m6-real-data-deploy.md` — Acceptance walk table; Global Constraints.
- `infra/deploy/VERIFY.md` (task-05), `infra/deploy/database.md` (task-04, the retention table),
  `docs/deployment.md`.
- Code you build on: `core/schemas/alert.py` (`SessionAlert`, `CowrieEvent` — the declared field
  set is `CowrieEvent.model_fields`; extras are in `event.model_extra`), `core/models/alerts.py`
  (`AlertRow.raw`, `received_at`, `status`), `core/services/alerts.py`, `scripts/seed_dev.py` +
  `tests/test_seed_dev.py` (the DB-backed script shape: `main(argv)` takes `--database-url` (else
  `DATABASE_URL`, else `TEST_DATABASE_URL`) and `--schema`, builds its own engine through
  `core.db.make_engine(url, schema=…)`, fails through `core/cli.py::fail("config_error", …)`; the
  tests load it via `importlib.util.spec_from_file_location` and pass the throwaway schema's URL +
  name — copy that shape exactly), `evals/scoring.py::percentile`
  (nearest-rank; import it — `scripts/` is outside the import-linter roots and no contract
  forbids a script importing `evals`),
  `.claude/skills/cowrie-fixture/references/cowrie-events.md` (the "summarized" eventid set and
  the "other events" list — the report flags any eventid outside BOTH).

## Files

- Create: `scripts/check_real_sessions.py`
- Create (test-author): `tests/test_check_real_sessions.py`
- Modify (controller/owner, at the soak): `infra/deploy/VERIFY.md` (real output),
  `infra/deploy/database.md` (retention numbers), `docs/deployment.md` (resources), `PRD.md`
  changelog v1.5 (any schema note), `SUGGESTIONS.md` (any fixture-vs-real divergence worth a
  fixture v2 later), `README.md` (Status: "M6 complete (live honeypot + Phase-1 deploy); M7 next"),
  `docs/plans/README.md` (M6 row → done), `docs/plans/m6-real-data-deploy.md` (Status → done)

## Interfaces

- **Consumes:** `SessionAlert`/`CowrieEvent`; `AlertRow`; `percentile`.
- **Produces exactly:**

  ```python
  # scripts/check_real_sessions.py
  # Usage (on the box, one-off, read-only):  docker compose run --rm api uv run python scripts/check_real_sessions.py --limit 200
  #        locally:                          uv run python scripts/check_real_sessions.py --limit 50   (DATABASE_URL from the env)
  SUMMARIZED_EVENTIDS: frozenset[str]   # the 11 ids in cowrie-events.md's per-event table
  OTHER_KNOWN_EVENTIDS: frozenset[str]  # the 7 ids in its "Other events" list
  @dataclass(frozen=True)
  class SessionReport:
      n_alerts: int; n_invalid: int                      # rows whose raw fails SessionAlert.model_validate (count + the ValidationError's first `loc`, never the value)
      eventid_counts: dict[str, int]                     # over all events
      unknown_eventids: dict[str, int]                   # ids in neither frozenset
      extra_fields_by_eventid: dict[str, list[str]]      # sorted field NAMES present in model_extra, per eventid (never values)
      n_truncated: int; truncated_events_total: int      # payloads carrying the shipper's "shipper.truncated_events"
      n_unclosed: int                                    # alerts with no cowrie.session.closed event (idle-flushed)
      events_per_session_p50: float; events_per_session_p95: float
      duration_ms_p50: float | None; duration_ms_p95: float | None   # over closed sessions
      raw_bytes_mean: float; raw_bytes_total: int        # len(json.dumps(raw)) — the storage number for database.md
      by_status: dict[str, int]                          # pending/triaged/failed over the sample
  async def collect(session_factory, *, limit: int) -> SessionReport     # newest `limit` alerts by received_at desc; read-only
  def render(report: SessionReport) -> str               # markdown: a summary table, then one table per section; field names only
  def main(argv: Sequence[str] | None = None) -> int
      # --limit N (default 200); --database-url URL (default: DATABASE_URL, else TEST_DATABASE_URL env); --schema NAME (tests: the throwaway schema; production: omitted);
      # builds engine/session factory exactly as scripts/seed_dev.py does, disposes the engine; exit 0; exit 1 via core.cli.fail("config_error", "no database URL (pass --database-url or set DATABASE_URL)") — never prints a URL
  ```

  The report's last section is **"Suggested follow-ups"** — mechanical, no judgment: one bullet
  per unknown eventid ("`<id>` seen N times — add to cowrie-events.md's 'other events' list"),
  one per extra field on a summarized eventid ("`<eventid>.<field>` seen — consider a synthetic
  fixture carrying it (v2 fixtures, M7) — NEVER edit an existing v1 fixture"), one if
  `n_invalid > 0` ("N payloads failed SessionAlert — inspect on the box: `select id, received_at
  from alerts where …` — the report never prints raw"), one if `n_truncated > 0`.

## Interfaces → test table

`tests/test_check_real_sessions.py` — DB fixtures; seeds through `tests/helpers.py::seed_alert`
(the five fixtures + hand-built variants); `_ATTACKER_STRINGS` from the seeded payloads (a
username, a command, a URL) must be absent from `render()`'s output.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| counts + known ids | `::test_collect_counts_eventids_and_flags_unknown` | seed the five fixtures + one alert whose raw carries an event `cowrie.made.up` (via `load_alert` + `model_dump` + edit + `insert_alert`) → `eventid_counts["cowrie.session.connect"] == 6`, `unknown_eventids == {"cowrie.made.up": 1}`, `SUMMARIZED_EVENTIDS` has 11 members and `OTHER_KNOWN_EVENTIDS` 7 |
| extra fields | `::test_extra_field_names_reported_never_values` | an event with `hassh: "deadbeef"` on `cowrie.client.kex` and `banana: "SECRET-VALUE"` on `cowrie.login.failed` → `extra_fields_by_eventid["cowrie.login.failed"] == ["banana"]`; `"SECRET-VALUE" not in render(report)`; note: `hassh` on `kex` IS declared in cowrie-events.md but not on `CowrieEvent` → it is reported as extra (the report is about `CowrieEvent`'s declared set, and the docstring says so) |
| truncated + unclosed | `::test_truncated_and_unclosed_sessions_counted` | one payload with `"shipper": {"version": "0.1.0", "truncated_events": 12}` → `n_truncated == 1`, `truncated_events_total == 12`; one payload without a closed event → `n_unclosed == 1`, `duration_ms_p50` computed over the closed ones only |
| percentiles + bytes | `::test_percentiles_and_raw_bytes` | with the five fixtures: `events_per_session_p50` equals the nearest-rank value the test computes from the fixtures' event counts; `raw_bytes_total == sum(len(json.dumps(raw)))` over the seeded rows (computed from the DB rows, not the files) |
| invalid rows | `::test_invalid_raw_is_counted_not_raised` | a row whose `raw` lacks `events` (inserted with a raw SQL `insert` in the test — bypassing the service on purpose, the docstring says why) → `n_invalid == 1`, `main` still exits 0 |
| limit + order | `::test_limit_takes_newest_by_received_at` | seed 7 with distinct `received_at`; `--limit 3` → `n_alerts == 3` and the three newest (by a marker `sensor` name) |
| CLI | `::test_main_prints_markdown_and_exits_zero`, `::test_main_exit_1_without_database_url` | `main(["--database-url", <tmp url>, "--schema", <tmp schema>, "--limit", "5"])` → 0, stdout contains `| eventid |` and `Suggested follow-ups`; with both env vars monkeypatched away and no flag → 1, stderr one line containing `no database URL`, and a canary URL value never printed |
| negative pin (rule 1) | `::test_render_never_contains_attacker_strings` | `all(s not in out for s in _ATTACKER_STRINGS)` |

## Steps (TDD)

- [ ] **Step 1 (RED — test-author):** `tests/test_check_real_sessions.py` per the table.
- [ ] **Step 2 (RED — test-author):** `uv run pytest -q tests/test_check_real_sessions.py` (with
  the export line) → Expected: collection error / `FileNotFoundError` on the missing script. Pin,
  commit `test(scripts): check_real_sessions report RED (m6 task-06)`.
- [ ] **Step 3 (GREEN — implementer): `scripts/check_real_sessions.py`** per Interfaces; run it
  against the dev stack seeded with `scripts/seed_dev.py` and paste the rendered report.
- [ ] **Step 4 (implementer): full gates (cold) → commit** `feat(scripts): check_real_sessions
  fixture-vs-real schema report (m6 task-06)`; path-scoped `git add scripts/check_real_sessions.py`.
- [ ] **Step 5 — Soak (controller + owner; after task-05's deploy):**
  1. T+0: `VERIFY.md` checks 0–2, 4–6, 9–10 executed and pasted; the honeypot's first sessions
     confirmed `triaged`; `check_real_sessions.py --limit 50` on the box → report pasted into
     the ledger; PRD/SUGGESTIONS notes written for every "Suggested follow-up" line.
  2. T+24 h (also record on the honeypot host: `systemctl show -p MemoryCurrent sentinelbrief-shipper`,
     `ls /var/lib/sentinelbrief-shipper/spool/dead | wc -l` — a non-empty `dead/` means sessions were
     permanently rejected and must be explained; task-02 review M9/notes): check 7 (the first nightly backup landed; `pg_database_size`), check 8 (rotated log
     files — the api's json log under attacker traffic), `check_real_sessions.py --limit 500` →
     `database.md`'s retention table filled (`rows/day`, `MB/day`, projected 90-day size).
  3. T+48 h: check 3 re-run (a fresh session end-to-end), check 6 re-run (the 48 h window), the
     stats endpoint's counts and a dashboard screenshot (owner) → the ledger; `VERIFY.md`,
     `database.md`, `docs/deployment.md` committed (`chore(deploy): m6 soak — <date>`).
- [ ] **Step 6 — Acceptance walk (controller):** every PRD §12 M6 clause → its evidence pointer,
  pasted under `Acceptance:` in the ledger (the spine's table is the checklist).
- [ ] **Step 7 — Housekeeping rows (rule 15):** `README.md` Status line, `docs/plans/README.md` M6
  row (`done — tag m6 (PR #7, <date>)` after the merge), spine `## Status` → done, ledger `Task 6:
  complete`. Then the milestone gate (`/milestone-gate m6`).

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_check_real_sessions.py                              # all pass, 0 skipped
uv run python scripts/check_real_sessions.py --limit 5 2>&1 | head -3               # with DATABASE_URL exported to the dev DB: the summary table's header; without: one stderr line, exit 1
grep -c "(recorded during deployment)" infra/deploy/VERIFY.md                       # at the soak's end: 0 for checks 0–10 (only check 11's "recorded at M8" placeholders remain)
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- `check_real_sessions.py` reports the real traffic's shape against the api's own schema without
  ever printing an attacker string or a label, and its findings are recorded as notes, never as
  silent fixture edits.
- `VERIFY.md` carries real output for checks 0–10 including the no-LLM log proof, a landed
  backup and a rehearsed restore, and rotated log files; 48 h of real traffic is visible at the
  public domain; the retention table has measured numbers.
- The PRD §12 M6 acceptance clauses are demonstrated in the ledger and the housekeeping rows are
  closed.
