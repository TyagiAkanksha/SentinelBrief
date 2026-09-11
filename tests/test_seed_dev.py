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

m5 task-03 (PRD §6.4) adds `test_fake_path_never_routes_even_with_strong_model_set` (GREEN on
arrival — the fake path's own `--live` gate already keeps routing off). m5 task-03 fix-1 (review
I1) adds `test_live_with_strong_equal_to_cheap_exit_1`, RED until Part B lands the `ValueError`
handling `--live` needs.

m5 task-05 re-pins `test_load_candidates_returns_fixtures_then_golden` for `load_candidates`'s new
3-tuple return (`SessionAlert`, canned verdict json, tool names) — `main()` no longer re-globs the
fixtures directory to line the per-candidate tool names up with `load_candidates`'s own
(separately re-globbed) fixture order, a duplication `test_main_no_longer_re_globs_the_fixtures_
directory_for_tool_names` pins away at the source level.
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


def test_fake_path_never_routes_even_with_strong_model_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_schema: tuple[str, str],
    seed_dev: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without `--live`, routing must stay off no matter what `STRONG_MODEL` says: a canned
    `FakeLLMClient` has no strong-tier reply scripted, so an escalation would exhaust its queue
    (PRD §6.4, m5 task-03)."""
    monkeypatch.setenv("STRONG_MODEL", "strong-x")
    url, schema = tmp_schema

    rc = seed_dev.main(["--database-url", url, "--schema", schema])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "created=25 skipped=0 failed=0\n"
    assert captured.err == ""
    assert _count_table(url, schema, "alerts") == 25
    assert _count_table(url, schema, "verdicts") == 25


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
    actual_first_five_session_ids = [
        alert.session_id for alert, _canned, _tool_names in candidates[:5]
    ]
    assert actual_first_five_session_ids == expected_first_five_session_ids

    # m5 task-05: each of the first five (fixture) rows carries its own `FIXTURE_TOOL_TURNS`
    # entry, in the same sorted-stem order `load_candidates` loads fixtures in.
    expected_first_five_tool_names = [
        seed_dev.FIXTURE_TOOL_TURNS.get(path.stem, ()) for path in fixture_paths
    ]
    actual_first_five_tool_names = [names for _alert, _canned, names in candidates[:5]]
    assert actual_first_five_tool_names == expected_first_five_tool_names

    golden_cases = load_golden(seed_dev.DEFAULT_GOLDEN)
    expected_golden_fingerprints = [case.case_id for case in golden_cases]
    actual_golden_fingerprints = [
        alert.fingerprint() for alert, _canned, _tool_names in candidates[5:]
    ]
    assert actual_golden_fingerprints == expected_golden_fingerprints

    # Every golden-set row carries no scripted tool turn (only the fixture rows do).
    actual_golden_tool_names = [names for _alert, _canned, names in candidates[5:]]
    assert actual_golden_tool_names == [()] * 20

    all_fingerprints = [alert.fingerprint() for alert, _canned, _tool_names in candidates]
    assert len(set(all_fingerprints)) == 25


def test_main_no_longer_re_globs_the_fixtures_directory_for_tool_names() -> None:
    """m5 task-05: `load_candidates` itself computes each candidate's tool names now (from its
    own, single glob of `fixtures`), so `main()` no longer needs a second, separately-globbed
    `fixture_stems`/`tool_names_by_candidate` pass to line them up."""
    text = SEED_SCRIPT.read_text()
    assert "fixture_stems" not in text
    assert "tool_names_by_candidate" not in text


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


# --- main: failure paths (check-order rows 1, 2, 5 — task-06 addendum, review finding I1) -------


def test_usage_error_exit_1(seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    """Check-order row 1: an unknown flag is a `usage` error, never argparse's own `SystemExit(2)`
    or a traceback — the brief's "one stderr line, exit 1" contract must hold here too."""
    rc = seed_dev.main(["--bogus-flag"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_settings_validation_error_exit_1(
    monkeypatch: pytest.MonkeyPatch, seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check-order row 2: a malformed `MODEL_PRICES_JSON` fails `Settings()` itself, before the
    database URL (or anything else) is even inspected — confirmed against `core/config.py`'s
    `_decode_model_prices_json` validator, which turns a `json.JSONDecodeError` into a pydantic
    `ValidationError`."""
    monkeypatch.setenv("MODEL_PRICES_JSON", "not-json")

    rc = seed_dev.main(["--database-url", "postgresql://unused"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")


def test_live_with_unpriced_model_exit_1(
    monkeypatch: pytest.MonkeyPatch, seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Check-order row 5: `--live` with a key present but `CHEAP_MODEL` absent from
    `MODEL_PRICES_JSON` fails inside `OpenAICompatibleLLMClient.from_settings` — "price before
    spend" (`worker/llm_client.py`). The bogus `--database-url` is never reached: if the check
    order were wrong, this would surface as `error: database_error:` instead."""
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("CHEAP_MODEL", "unpriced-model")
    monkeypatch.delenv("MODEL_PRICES_JSON", raising=False)

    rc = seed_dev.main(["--live", "--database-url", "postgresql://unused"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "MODEL_PRICES_JSON" in lines[0]
    assert "unpriced-model" in lines[0]


def test_live_with_strong_equal_to_cheap_exit_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_schema: tuple[str, str],
    seed_dev: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """m5 task-03 fix-1 (review I1): `--live` with `STRONG_MODEL == CHEAP_MODEL` (both priced, so
    `OpenAICompatibleLLMClient.from_settings` itself is happy) must still fail as a clean
    `config_error` — `TriagePipeline.__init__`'s own equal-id `ValueError` (raised from inside
    `seed()`, after the first alert's `insert_alert`/commit) must never escape
    `asyncio.run(seed(...))` as a traceback. A REAL, reachable `tmp_schema` is required here
    (unlike the bogus `--database-url` other `--live` failure-path tests use): this failure fires
    from inside `seed()`'s per-alert loop, after the first `insert_alert` already succeeded — an
    unreachable database would surface as `error: database_error:` first and never exercise the
    `ValueError` path this test pins."""
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv("STRONG_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )
    url, schema = tmp_schema

    rc = seed_dev.main(["--live", "--database-url", url, "--schema", schema])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: config_error:")
    assert "Traceback" not in captured.err


# --- never referenced from compose (DB-less; no `seed_dev` fixture -> green on arrival) ---------


def test_seed_script_is_not_referenced_by_compose() -> None:
    compose_text = (REPO_ROOT / "infra" / "docker-compose.yml").read_text()
    assert "seed_dev" not in compose_text
