"""Pins `scripts/seed_dev.py`'s `main(argv, *, llm=…)` contract (m3 task-06).

`scripts/` has no `__init__.py`, so the module is loaded via
`importlib.util.spec_from_file_location` (`tests/test_post_alert.py`'s pattern) through the
`seed_dev` fixture below — never at module import time, so `test_seed_script_is_not_referenced_by_
compose` (which never requests that fixture) can collect and pass even before the script exists.

Every other test in this module requests `seed_dev` and therefore errors at fixture setup with
`FileNotFoundError: .../scripts/seed_dev.py` until task-06's implementer lands the script (RED).

DB-touching tests are **sync** and request only the sync `tmp_schema` fixture (`main()` runs its
own `asyncio.run`, which cannot nest inside pytest-asyncio's already-running loop —
`tests/conftest.py`); they count rows and inspect columns through the module-private psycopg
helpers below rather than the async `tests.helpers` row counters, since `main()`'s own connection
and this test's assertions must go through two independent connections anyway.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict
from evals.golden import load_golden
from tests.fakes import FakeLLMClient
from tests.helpers import load_alert

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_dev.py"

# A database URL that is never expected to connect: syntactically valid, port 1 refuses
# immediately (never hangs), so it proves a code path never reaches (or fails at) the database.
_UNREACHABLE_DATABASE_URL = "postgresql://sentinel:sentinel@127.0.0.1:1/nope"


def _load_seed_dev() -> ModuleType:
    """Load `scripts/seed_dev.py` as a standalone module (no package `__init__.py` exists)."""
    spec = importlib.util.spec_from_file_location("seed_dev", SEED_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed_dev() -> ModuleType:
    """The freshly (re)loaded `scripts/seed_dev.py` module, one load per test."""
    return _load_seed_dev()


def _count_table(url: str, schema: str, table: str) -> int:
    """`SELECT count(*) FROM "<schema>".<table>` via a fresh sync psycopg connection.

    A connection independent of whatever `main()` opened internally — this always counts what is
    actually durable in the database, not what one particular session happens to see.
    """
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{schema}".{table}')
        (count,) = cur.fetchone()
    return count


def _query_all(url: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    """Run `sql` (schema already embedded by the caller) and return every result row."""
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# --- happy path, idempotence, golden label fidelity (DB) ---------------------------------------


def test_seed_creates_25_rows_with_a_verdict_each(
    tmp_schema: tuple[str, str], seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    url, schema = tmp_schema

    rc = seed_dev.main(["--database-url", url, "--schema", schema])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "created=25 skipped=0 failed=0\n"
    assert captured.err == ""
    assert _count_table(url, schema, "alerts") == 25
    assert _count_table(url, schema, "verdicts") == 25

    statuses = _query_all(url, f'SELECT status FROM "{schema}".alerts')
    assert all(status == "triaged" for (status,) in statuses)

    model_primaries = _query_all(url, f'SELECT model_primary FROM "{schema}".verdicts')
    assert all(model == seed_dev.FAKE_MODEL for (model,) in model_primaries)


def test_seed_is_idempotent(
    tmp_schema: tuple[str, str], seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    url, schema = tmp_schema

    first_rc = seed_dev.main(["--database-url", url, "--schema", schema])
    capsys.readouterr()  # discard the first run's output; only the second run is asserted below
    assert first_rc == 0

    second_rc = seed_dev.main(["--database-url", url, "--schema", schema])

    captured = capsys.readouterr()
    assert second_rc == 0
    assert captured.out == "created=0 skipped=25 failed=0\n"
    assert captured.err == ""
    assert _count_table(url, schema, "alerts") == 25
    assert _count_table(url, schema, "verdicts") == 25


def test_seed_verdicts_match_golden_labels(
    tmp_schema: tuple[str, str], seed_dev: ModuleType
) -> None:
    url, schema = tmp_schema

    rc = seed_dev.main(["--database-url", url, "--schema", schema])
    assert rc == 0

    golden_cases = load_golden(seed_dev.DEFAULT_GOLDEN)
    assert len(golden_cases) == 20  # sanity: the fidelity check below is not vacuous
    for case in golden_cases:
        rows = _query_all(
            url,
            f"SELECT v.severity, v.category, v.escalate "
            f'FROM "{schema}".verdicts v JOIN "{schema}".alerts a ON v.alert_id = a.id '
            f"WHERE a.fingerprint = %s",
            (case.case_id,),
        )
        assert len(rows) == 1, f"expected exactly one verdict for golden case {case.case_id}"
        severity, category, escalate = rows[0]
        assert severity == case.label.severity
        assert category == case.label.category
        assert escalate == case.label.escalate


# --- failed triage counted, not fatal (DB, `llm=` seam) -----------------------------------------


def test_failed_triage_is_counted_not_fatal(
    tmp_schema: tuple[str, str], seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    url, schema = tmp_schema
    fake = FakeLLMClient(["{}"] * 50)  # 25 alerts x 2 attempts each (PRD §6.5 one retry)

    rc = seed_dev.main(["--database-url", url, "--schema", schema], llm=fake)

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "created=25 skipped=0 failed=25\n"
    assert captured.err == ""
    assert _count_table(url, schema, "alerts") == 25
    assert _count_table(url, schema, "verdicts") == 0

    statuses = _query_all(url, f'SELECT status FROM "{schema}".alerts')
    assert all(status == "failed" for (status,) in statuses)


# --- canned_verdict / load_candidates (DB-less) --------------------------------------------------


def test_canned_verdict_cites_src_ip_and_username_and_validates(seed_dev: ModuleType) -> None:
    alert = load_alert("alert4")
    severity, category, escalate = seed_dev.FIXTURE_LABELS["alert4"]
    assert (severity, category, escalate) == (4, "successful_intrusion", True)

    reply = seed_dev.canned_verdict(alert, severity=severity, category=category, escalate=escalate)

    verdict = Verdict.model_validate_json(reply)
    assert verdict.severity == 4
    assert verdict.category == "successful_intrusion"
    assert verdict.escalate is True
    assert alert.src_ip in verdict.reasoning
    first_username = next(e.username for e in alert.events if e.username is not None)
    assert first_username in verdict.reasoning
    sentences = [s.strip() for s in verdict.reasoning.split(". ") if s.strip()]
    assert len(sentences) == 2


def test_load_candidates_returns_fixtures_then_golden(seed_dev: ModuleType) -> None:
    candidates = seed_dev.load_candidates(
        golden=seed_dev.DEFAULT_GOLDEN, fixtures=seed_dev.DEFAULT_FIXTURES
    )

    assert len(candidates) == 25

    fixture_paths = sorted(seed_dev.DEFAULT_FIXTURES.glob("*.json"))
    assert len(fixture_paths) == 5
    expected_first_five_session_ids = [
        SessionAlert.model_validate(json.loads(path.read_text())).session_id
        for path in fixture_paths
    ]
    actual_first_five_session_ids = [alert.session_id for alert, _ in candidates[:5]]
    assert actual_first_five_session_ids == expected_first_five_session_ids

    golden_cases = load_golden(seed_dev.DEFAULT_GOLDEN)
    expected_golden_fingerprints = [case.case_id for case in golden_cases]
    actual_golden_fingerprints = [alert.fingerprint() for alert, _ in candidates[5:]]
    assert actual_golden_fingerprints == expected_golden_fingerprints

    all_fingerprints = [alert.fingerprint() for alert, _ in candidates]
    assert len(set(all_fingerprints)) == 25


# --- main: failure paths (task-06 brief's check-order table) ------------------------------------


def test_live_refused_without_api_key(
    monkeypatch: pytest.MonkeyPatch, seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    rc = seed_dev.main(["--live", "--database-url", "postgresql://unused"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == "error: config_error: --live requires LLM_API_KEY\n"


def test_missing_golden_file_exit_1(
    tmp_path: Path, seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist.jsonl"

    rc = seed_dev.main(["--database-url", _UNREACHABLE_DATABASE_URL, "--golden", str(missing)])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_golden:")


def test_missing_fixtures_dir_exit_1(
    tmp_path: Path, seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_dir = tmp_path / "no-such-dir"

    rc = seed_dev.main(
        ["--database-url", _UNREACHABLE_DATABASE_URL, "--fixtures", str(missing_dir)]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: invalid_fixtures:")


def test_no_database_url_exit_1(
    monkeypatch: pytest.MonkeyPatch, seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    rc = seed_dev.main([])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert (
        captured.err
        == "error: config_error: no database URL (pass --database-url or set DATABASE_URL)\n"
    )


def test_unreachable_database_exit_1(
    seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = seed_dev.main(["--database-url", _UNREACHABLE_DATABASE_URL])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: database_error:")


# --- never referenced from compose (DB-less; no `seed_dev` fixture -> green on arrival) ---------


def test_seed_script_is_not_referenced_by_compose() -> None:
    compose_text = (REPO_ROOT / "infra" / "docker-compose.yml").read_text()
    assert "seed_dev" not in compose_text
