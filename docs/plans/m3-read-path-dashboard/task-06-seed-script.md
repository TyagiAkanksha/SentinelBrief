---
id: task-06
milestone: m3-read-path-dashboard
depends_on: [task-02]
status: planned
spec: PRD.md §6.1 (insert + dedup), §6.2 (one transaction per verdict), §10.1 (no public path computes — the seed is a dev script, never a service), §12 M3 ("seeded DB renders a browsable queue locally"), §13 (v1 labels are synthetic, never published); CONVENTIONS.md §3, §10 (`FakeLLMClient` is the only LLM double; CLIs take `main(argv, *, llm=…)`)
---

# task-06 — `scripts/seed_dev.py`: fixtures + golden v1 through `insert_alert` + `triage_alert` with `FakeLLMClient` (`--live` opt-in)

## Goal

`uv run python scripts/seed_dev.py` fills a local database with the five `fixtures/alerts/*.json`
sessions and the twenty `evals/golden/v1.jsonl` sessions — 25 unique fingerprints, verified at
briefing time — by running each one through the **production** write path (`insert_alert` →
`TriagePipeline.triage_alert` → `persist_verdict`), so seeded rows are shaped exactly like ingested
ones. By default the LLM is `tests.fakes.FakeLLMClient` replaying a realistic canned verdict built
from each golden label (or a per-fixture table), so the queue shows all five severity bands, seven
categories, and reasoning that cites the source IP and a username; `--live` opts into the real
client and refuses to run without `LLM_API_KEY`. A second run creates nothing. The script is never
referenced from any compose `command:` (M2 final review, plan defect 9).

## Context (read ONLY these)

- `PRD.md` §6.1, §6.2, §10.1, §12 M3, §13.
- `CONVENTIONS.md` §3, §7, §10; `.claude/rules/tests.md` (CLIs are driven through
  `main(argv, *, llm=…)`; `FakeLLMClient` is the only LLM double).
- Code you build on: `core/services/alerts.py::insert_alert`, `worker/triage.py::TriagePipeline`
  (`triage_alert` owns its commits), `worker/llm_client.py::OpenAICompatibleLLMClient.from_settings`,
  `core/config.py::Settings`, `core/db.py` (`make_engine(url, schema=…)`, `make_session_factory`),
  `core/schemas/alert.py::SessionAlert`, `core/schemas/verdict.py::Verdict` / `VerdictCategory`,
  `evals/golden/__init__.py` (`load_golden`, `GoldenCase`), `tests/fakes.py::FakeLLMClient`,
  `worker/triage_one.py` and `evals/run.py` (the `_fail` / one-stderr-line convention and the
  `_Parser` usage-error pattern to mirror), `scripts/post_alert.py`, `tests/test_post_alert.py`
  (script import via `importlib.util.spec_from_file_location`), `tests/conftest.py`
  (`tmp_schema` is sync — the seed's tests are sync), `fixtures/alerts/README.md` (the intended
  severity band per fixture), `infra/docker-compose.yml`.
- Task-01 `tests/helpers.py` (`load_alert`, `count_rows`) is available but the seed tests count
  through psycopg because `main()` runs its own event loop (see the table).

## Files

- Create: `scripts/seed_dev.py`
- Create (test-author): `tests/test_seed_dev.py`
- Modify: none (`README.md` gains the seed step in task-07; no compose file references the script)

## Interfaces

- **Consumes:** `insert_alert`, `IngestResult`, `TriagePipeline`, `OpenAICompatibleLLMClient`,
  `Settings`, `make_engine`, `make_session_factory`, `SessionAlert`, `Verdict`,
  `VerdictCategory`, `load_golden`, `GoldenCase`, `FakeLLMClient`, `LLMClient`, `ConfigError`.
- **Produces (task-07 and the README rely on — produce exactly):**

  ```python
  # scripts/seed_dev.py — dev-only; runs from a repo checkout on the host (it imports tests.fakes),
  # never inside the api image and never from a compose `command:` (PRD §10.1).
  REPO_ROOT = Path(__file__).resolve().parents[1]
  DEFAULT_GOLDEN = REPO_ROOT / "evals" / "golden" / "v1.jsonl"
  DEFAULT_FIXTURES = REPO_ROOT / "fixtures" / "alerts"
  FAKE_MODEL = "seed-fake"                       # model_primary/model_final on fake-seeded verdicts, so they are recognizable

  FIXTURE_LABELS: dict[str, tuple[int, VerdictCategory, bool]] = {   # per fixtures/alerts/README.md bands
      "alert1": (1, "scanning", False), "alert2": (2, "brute_force", False), "alert3": (3, "brute_force", False),
      "alert4": (4, "successful_intrusion", True), "alert5": (5, "malware_delivery", True),
  }
  DEFAULT_FIXTURE_LABEL: tuple[int, VerdictCategory, bool] = (2, "other", False)   # any other *.json in --fixtures
  CONFIDENCE_BY_SEVERITY: dict[int, float] = {1: 0.85, 2: 0.80, 3: 0.75, 4: 0.90, 5: 0.95}
  PHRASE_BY_CATEGORY: dict[VerdictCategory, str] = {
      "scanning": "probed the SSH service and disconnected",
      "brute_force": "attempted repeated credential logins",
      "successful_intrusion": "logged in and ran commands",
      "malware_delivery": "logged in and fetched a remote payload",
      "persistence_attempt": "logged in and installed a persistence mechanism",
      "reconnaissance": "enumerated the host",
      "other": "produced an unclassified session",
  }
  ACTION_BY_CATEGORY: dict[VerdictCategory, str] = {
      "scanning": "No action; keep monitoring the source range.",
      "brute_force": "Block the source IP at the edge and confirm no login succeeded.",
      "successful_intrusion": "Isolate the host, rotate the exposed credentials, and review the command history.",
      "malware_delivery": "Isolate the host, capture the downloaded payload hash, and block the download URL.",
      "persistence_attempt": "Isolate the host and remove the persistence mechanism (cron, keys, services).",
      "reconnaissance": "Review what was enumerated and watch the source IP for follow-up activity.",
      "other": "Review the session manually.",
  }

  def canned_verdict(alert: SessionAlert, *, severity: int, category: VerdictCategory, escalate: bool) -> str: ...
      # username = first event with a non-null `username`, else "(none)"
      # reasoning = f"{alert.src_ip} {PHRASE_BY_CATEGORY[category]} against sensor {alert.sensor} in session {alert.session_id}. "
      #             f"Username {username!r} was observed; {len(alert.events)} events were recorded."
      # returns json.dumps(Verdict(severity=..., category=..., confidence=CONFIDENCE_BY_SEVERITY[severity], reasoning=...,
      #                            recommended_action=ACTION_BY_CATEGORY[category], escalate=escalate).model_dump())
      # — built through `Verdict`, so the canned reply always validates (PRD §6.6 escalate rule included)

  def load_candidates(*, golden: Path, fixtures: Path) -> list[tuple[SessionAlert, str]]: ...
      # fixtures first: every fixtures/*.json sorted by name -> (SessionAlert, canned_verdict(... FIXTURE_LABELS.get(stem, DEFAULT_FIXTURE_LABEL)))
      # then golden rows in file order -> (case.alert, canned_verdict(case.alert, severity=case.label.severity, category=case.label.category, escalate=case.label.escalate))
      # raises ValueError/OSError from load_golden / fixture parsing (mapped to exit 1 by main)

  @dataclass(frozen=True)
  class SeedCounts:
      created: int; skipped: int; failed: int

  async def seed(candidates: Sequence[tuple[SessionAlert, str]], *, database_url: str, schema: str | None,
                 llm: LLMClient | None, model: str, prompt_version: str) -> SeedCounts: ...
      # engine = make_engine(database_url, schema=schema); factory = make_session_factory(engine)
      # async with factory() as session: for alert, canned in candidates:
      #     result = await insert_alert(session, alert); await session.commit()          # same commit-before-triage as the ingest route
      #     if not result.created: skipped += 1; continue                                  # duplicates never re-trigger triage (PRD §6.1)
      #     client = llm if llm is not None else FakeLLMClient([canned])                   # one fresh fake per alert -> the queue can never desync on a skip
      #     pipeline = TriagePipeline(llm=client, model=model, prompt_version=prompt_version)
      #     status = await pipeline.triage_alert(session, result.alert_id)                 # commits itself; "triaged" | "failed"
      #     created += 1; failed += status == "failed"
      # finally: await engine.dispose()

  def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int: ...
      # flags: --database-url URL   default os.environ["DATABASE_URL"] else os.environ["TEST_DATABASE_URL"] else ""
      #        --schema NAME        optional; make_engine(url, schema=NAME) — how the tests point the script at their throwaway
      #                             schema (chosen over a search_path option in the URL: same mechanism as the fixtures and
      #                             alembic's MIGRATE_SCHEMA, no URL-encoding pitfalls)
      #        --live               real OpenAICompatibleLLMClient.from_settings(Settings()); model = settings.cheap_model
      #        --golden PATH        default DEFAULT_GOLDEN
      #        --fixtures DIR       default DEFAULT_FIXTURES
      # check order (each failure prints exactly one `error: <code>: <message>` line to stderr, exit 1, nothing else on stdout):
      #   1 usage error (unknown flag)                                    -> "usage"
      #   2 Settings() ValidationError                                    -> "config_error"
      #   3 empty database URL                                            -> "config_error: no database URL (pass --database-url or set DATABASE_URL)"
      #   4 --live with settings.llm_api_key empty                        -> "config_error: --live requires LLM_API_KEY"   (checked BEFORE any DB or network access)
      #   5 --live: from_settings raises ConfigError (unpriced model)     -> "config_error: <message>"
      #   6 golden file missing/invalid (load_golden raises)              -> "invalid_golden: <message>"
      #   7 fixtures dir missing / a fixture fails SessionAlert validation -> "invalid_fixtures: <message>"
      #   8 database unreachable (OSError / SQLAlchemyError from seed())  -> "database_error: <message>"
      # llm= (tests) beats --live; without either the fake path is used with model=FAKE_MODEL; prompt_version = settings.triage_prompt_version
      # success: print(f"created={c.created} skipped={c.skipped} failed={c.failed}"); return 0 (failed triages are reported, not fatal)
  if __name__ == "__main__": raise SystemExit(main())
  ```

## Interfaces → test table

`tests/test_seed_dev.py` loads the script with `importlib.util.spec_from_file_location` (as
`tests/test_post_alert.py` does). Tests that touch the database are **sync** and request the sync
`tmp_schema` fixture only — `main()` runs its own `asyncio.run`, which cannot nest inside
pytest-asyncio's loop — and count rows with a module-private psycopg helper
`_count_table(url, schema, table) -> int` (`SELECT count(*) FROM "<schema>".<table>`); every test
passes `--database-url <url> --schema <schema>` from `tmp_schema`.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| happy path, 25 rows, verdict each | `tests/test_seed_dev.py::test_seed_creates_25_rows_with_a_verdict_each` | exit 0; stdout exactly `created=25 skipped=0 failed=0`; `alerts` 25, `verdicts` 25, every `alerts.status == 'triaged'`, every `model_primary == 'seed-fake'` |
| idempotence | `tests/test_seed_dev.py::test_seed_is_idempotent` | second run → `created=0 skipped=25 failed=0`; counts unchanged |
| golden label fidelity | `tests/test_seed_dev.py::test_seed_verdicts_match_golden_labels` | for every golden case, the verdict joined on `fingerprint` has the label's severity/category/escalate |
| `canned_verdict` cites evidence | `tests/test_seed_dev.py::test_canned_verdict_cites_src_ip_and_username_and_validates` (DB-less) | reply parses as `Verdict`; reasoning contains `alert.src_ip`, the first username, two sentences; fixture `alert4` → severity 4, `successful_intrusion`, escalate true |
| `load_candidates` order + count | `tests/test_seed_dev.py::test_load_candidates_returns_fixtures_then_golden` (DB-less) | 25 candidates; first five are the fixtures by name; all fingerprints unique |
| `--live` refused | `tests/test_seed_dev.py::test_live_refused_without_api_key` (DB-less; `monkeypatch.delenv("LLM_API_KEY")`, `--database-url postgresql://unused`) | exit 1; stderr `error: config_error: --live requires LLM_API_KEY`; stdout empty |
| missing golden | `tests/test_seed_dev.py::test_missing_golden_file_exit_1` (DB-less, bogus `--database-url`) | exit 1; stderr starts `error: invalid_golden:` |
| missing fixtures dir | `tests/test_seed_dev.py::test_missing_fixtures_dir_exit_1` (DB-less) | exit 1; stderr starts `error: invalid_fixtures:` |
| no database URL | `tests/test_seed_dev.py::test_no_database_url_exit_1` (DB-less; both env names deleted) | exit 1; stderr `error: config_error: no database URL …` |
| unreachable database | `tests/test_seed_dev.py::test_unreachable_database_exit_1` (DB-less; `--database-url postgresql://sentinel:sentinel@127.0.0.1:1/nope`) | exit 1; stderr starts `error: database_error:` |
| failed triage counted, `llm=` seam | `tests/test_seed_dev.py::test_failed_triage_is_counted_not_fatal` (`llm=FakeLLMClient(["{}"] * 50)`) | exit 0; `created=25 skipped=0 failed=25`; 0 verdict rows; every status `failed` |
| never in compose | `tests/test_seed_dev.py::test_seed_script_is_not_referenced_by_compose` (DB-less) | `infra/docker-compose.yml` text does not contain `seed_dev` |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins `tests/test_seed_dev.py`; the **implementer**
does Steps 3–5 and never edits it.

- [ ] **Step 1 (RED — test-author): write `tests/test_seed_dev.py`** per the table (12 tests).
  Before writing the 25-row assertion, confirm the count once: `uv run python -c "import json,
  pathlib; from core.schemas.alert import SessionAlert; from evals.golden import load_golden;
  f=[SessionAlert.model_validate(json.loads(p.read_text())).fingerprint() for p in
  sorted(pathlib.Path('fixtures/alerts').glob('*.json'))]+[c.case_id for c in
  load_golden(pathlib.Path('evals/golden/v1.jsonl'))]; print(len(f), len(set(f)))"` → `25 25`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_seed_dev.py` → Expected: every test errors at the module load with
  `FileNotFoundError: .../scripts/seed_dev.py` (or `AttributeError` for the module attributes),
  except `test_seed_script_is_not_referenced_by_compose`, which is green on arrival — record it.
  Pin, commit `test(scripts): seed_dev CLI RED (m3 task-06)`.
- [ ] **Step 3 (GREEN — implementer): implement `scripts/seed_dev.py`** per Interfaces. Import
  `tests.fakes.FakeLLMClient` lazily inside the fake branch (the module must still import inside
  the api image, where `tests/` is absent). `uv run mypy scripts/seed_dev.py` clean even though
  `scripts/` is outside the mypy gate (CONVENTIONS §9) — the report pastes the output.
- [ ] **Step 4 (implementer): run it for real against the M2 compose stack** — `docker compose -f
  infra/docker-compose.yml up -d postgres api`, migrate, then `uv run python scripts/seed_dev.py
  --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief` → paste
  `created=25 skipped=0 failed=0`, run again → `created=0 skipped=25 failed=0`, and
  `curl -s 'localhost:8000/api/v1/alerts?page_size=3' | python3 -m json.tool | head -40` into
  the report (S5 rows first).
- [ ] **Step 5 (implementer): full gates → commit:**
  `feat(scripts): seed_dev.py loads fixtures + golden v1 through the real pipeline (m3 task-06)`
  with the two trailers; path-scoped `git add scripts/seed_dev.py`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_seed_dev.py                                    # 12 passed
uv run python scripts/seed_dev.py --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief   # created=25 skipped=0 failed=0  (against the compose Postgres, migrated)
uv run python scripts/seed_dev.py --database-url postgresql://sentinel:sentinel@127.0.0.1:5432/sentinelbrief   # created=0 skipped=25 failed=0
LLM_API_KEY= uv run python scripts/seed_dev.py --live --database-url postgresql://unused; echo "exit=$?"       # error: config_error: --live requires LLM_API_KEY / exit=1
grep -c seed_dev infra/docker-compose.yml                                  # 0
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean
```

## Acceptance

- One command seeds 25 triaged alerts (5 fixtures + 20 golden v1) through `insert_alert` +
  `triage_alert` with the fake LLM; the verdicts carry the golden labels and reasoning that cites
  the source IP; a re-run creates 0; `--live` is explicit and refuses without a key; every failure
  path is one stderr line and exit 1; nothing in compose references the script.
