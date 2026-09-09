"""Pins `scripts/seed_dev.py`'s tool traces (PRD §7.2, §12 M4; m4 task-06): the five fixture
alerts get one scripted tool turn each (`FIXTURE_TOOL_TURNS`), replayed against
`tests/fixtures/tools/`, so the dev database carries real `tool_calls` rows for task-07's
timeline; every golden-set row scripts none.

`scripts/` has no `__init__.py`, so the module is loaded via
`importlib.util.spec_from_file_location` (mirrors `tests/test_seed_dev.py`'s `_load_seed_dev`
fixture; test files never import from each other, so it is duplicated here). DB-touching tests
are **sync** and request only the sync `tmp_schema` fixture (`main()` runs its own
`asyncio.run`, which cannot nest inside pytest-asyncio's already-running loop).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import load_golden

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_dev.py"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "alerts"


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
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{schema}".{table}')
        (count,) = cur.fetchone()
    return count


def _query_all(url: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _alert_fingerprint(name: str) -> str:
    payload = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return SessionAlert.model_validate(payload).fingerprint()


def _tool_calls_for_fingerprint(
    url: str, schema: str, fingerprint: str
) -> list[tuple[str, dict[str, Any]]]:
    rows = _query_all(
        url,
        f'SELECT tc.tool_name, tc.result FROM "{schema}".tool_calls tc '
        f'JOIN "{schema}".verdicts v ON tc.verdict_id = v.id '
        f'JOIN "{schema}".alerts a ON v.alert_id = a.id '
        f"WHERE a.fingerprint = %s ORDER BY tc.seq",
        (fingerprint,),
    )
    return [(name, result) for name, result in rows]


def test_seed_fixture_alerts_carry_the_scripted_tool_traces(
    tmp_schema: tuple[str, str], seed_dev: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    url, schema = tmp_schema

    rc = seed_dev.main(["--database-url", url, "--schema", schema])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "created=25 skipped=0 failed=0\n"
    assert captured.err == ""
    assert _count_table(url, schema, "alerts") == 25

    alert4_calls = _tool_calls_for_fingerprint(url, schema, _alert_fingerprint("alert4"))
    assert [name for name, _ in alert4_calls] == ["get_session_commands", "get_ip_geo_asn"]
    assert alert4_calls[1][1]["country"] == "DE"

    alert1_calls = _tool_calls_for_fingerprint(url, schema, _alert_fingerprint("alert1"))
    assert [name for name, _ in alert1_calls] == ["get_ip_geo_asn"]
    assert alert1_calls[0][1]["country"] == "NL"

    golden_cases = load_golden(seed_dev.DEFAULT_GOLDEN)
    assert len(golden_cases) == 20  # sanity: the zero-trace check below is not vacuous
    for case in golden_cases:
        assert _tool_calls_for_fingerprint(url, schema, case.case_id) == []


def test_seed_live_flag_selects_live_recorder(seed_dev: ModuleType) -> None:
    from tests.helpers import load_alert
    from worker.tools import LiveToolRecorder, ReplayToolRecorder, ToolContext

    live = seed_dev.select_recorder(live=True)
    assert isinstance(live, LiveToolRecorder)

    replay = seed_dev.select_recorder(live=False)
    assert isinstance(replay, ReplayToolRecorder)

    class _GeoStub:
        """Stands in for `GeoAsnTool`: `external=True`, so `ReplayToolRecorder` must serve it
        from a fixture, never calling `run` (which would raise if it ever were)."""

        name = "get_ip_geo_asn"
        description = "stub"
        parameters: dict[str, object] = {}
        external = True

        async def run(self, arguments: object, ctx: object) -> dict[str, object]:
            raise AssertionError("must not run live for an external tool")

    alert = load_alert("alert1")
    ctx = ToolContext(alert=alert, session=None, now=datetime.now(UTC))

    async def _probe() -> dict[str, object]:
        return await replay.execute(_GeoStub(), {"ip": "203.0.113.10"}, ctx)

    result = asyncio.run(_probe())
    assert result.get("country") == "NL"
