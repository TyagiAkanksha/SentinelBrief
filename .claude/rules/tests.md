---
paths: tests/**
---

# Rules for `tests/`

- No `__init__.py` anywhere under `tests/`; unique test-file basenames across the tree; test
  functions are named for the behavior they pin (`test_duplicate_post_200_same_id_no_new_row`).
- **Authored tests are pinned.** A file the test-author committed for the current task carries a
  sha256 in their report; the implementer stops and asks before editing it, even for formatting.
  Adding new test files is always allowed.
- Simulate real usage: drive the API through `httpx.AsyncClient(transport=ASGITransport(app))`,
  the CLIs through their `main(argv, *, llm=…)` entrypoints, the DB through the throwaway-schema
  fixtures. **Mock only external seams** — `tests/fakes.py::FakeLLMClient` (the only LLM double),
  AbuseIPDB, the clock. Never mock our own services or models.
- `FakeLLMClient` replays queued responses and validates them through the same
  `core.llm.parse_structured` the real client uses, so a bad JSON reply fails identically.
- DB fixtures (`tmp_schema`, `db_engine`, `db_session_factory`, `db_session`) skip **by name**
  when `TEST_DATABASE_URL` is unset — recorded as skips, never passes. A green run without the
  export line is not evidence; include the export line in every pasted gate output. Point the
  variable at a dedicated `sentinelbrief_test` database.
- `tmp_schema` is a **sync** fixture (psycopg autocommit + `alembic.command.upgrade`); do not make
  it async — `alembic/env.py` calls `asyncio.run` and cannot nest in the test loop.
- `@pytest.mark.live` marks anything that calls a real external API; the default `addopts`
  excludes it, and CI never runs it. Tool tests (M4+) replay `tests/fixtures/tools/*.json`.
- Gates-as-tests (`test_lint_clean.py`, `test_import_contracts.py`) call the tools by subprocess
  with no path args so they stay aligned with the canonical config.
- Every bug fix ships a regression test in the same commit; every new setting ships its
  `.env.example` line (checked by `test_env_example_roster.py`).
- Never commit a real attacker payload, a secret, or a live API response body into `tests/`.
