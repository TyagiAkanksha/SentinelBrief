"""Pins `evals.sample`'s CLI `--since`/`--exclude` flags (whole-branch fix wave, finding t01 CM2).

`tests/test_sample.py` exercises `sample()` directly with `since=`/`exclude_case_ids=`; this module
covers the CLI parsing paths those never reach — `_parse_since` (a `--since` date string) and
`_load_exclude_case_ids` (a `--exclude` candidate JSONL). The `--exclude` guard is load-bearing
for the planned injection re-sample (never re-offer an already-labeled case).

CLI (`main`) tests mirror `tests/test_sample.py`'s own convention: a sync `tmp_schema` fixture,
seeding through a nested `asyncio.run` call that builds and disposes its own engine, exactly as
`main()` itself does. `_verdict` is a small, local factory (test files never import from each
other, `.claude/rules/tests.md`).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.db import make_engine, make_session_factory
from core.schemas.verdict import Verdict
from evals.sample import main
from tests.helpers import seed_alert


def _verdict() -> Verdict:
    return Verdict(
        severity=2,
        category="scanning",
        confidence=0.9,
        reasoning="synthetic reasoning for the --since/--exclude CLI tests, never scored.",
        recommended_action="synthetic recommended action.",
        escalate=False,
    )


def _session_ids(out_path: Path) -> set[str]:
    return {
        json.loads(line)["alert"]["session_id"]
        for line in out_path.read_text().splitlines()
        if line.strip()
    }


def _case_ids(out_path: Path) -> set[str]:
    return {
        json.loads(line)["case_id"] for line in out_path.read_text().splitlines() if line.strip()
    }


def test_main_since_filters_earlier_alerts(tmp_schema: tuple[str, str], tmp_path: Path) -> None:
    url, schema = tmp_schema
    cutoff = datetime(2026, 9, 10, tzinfo=UTC)

    async def _seed() -> None:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                await seed_alert(
                    session,
                    "alert1",
                    verdict=_verdict(),
                    session_id="since-old",
                    received_at=cutoff - timedelta(days=5),
                )
                await seed_alert(
                    session,
                    "alert1",
                    verdict=_verdict(),
                    session_id="since-new",
                    received_at=cutoff + timedelta(days=1),
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    out_path = tmp_path / "candidates.jsonl"

    rc = main(
        [
            "--database-url",
            url,
            "--schema",
            schema,
            "--n",
            "10",
            "--seed",
            "1",
            "--out",
            str(out_path),
            "--since",
            "2026-09-10",
        ]
    )

    assert rc == 0
    session_ids = _session_ids(out_path)
    assert "since-new" in session_ids
    assert "since-old" not in session_ids


def test_main_exclude_drops_case_ids_from_a_candidate_file(
    tmp_schema: tuple[str, str], tmp_path: Path
) -> None:
    url, schema = tmp_schema

    async def _seed() -> None:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                for i in range(6):
                    await seed_alert(
                        session, "alert1", verdict=_verdict(), session_id=f"excl-cli-{i}"
                    )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    first_out = tmp_path / "first.jsonl"
    second_out = tmp_path / "second.jsonl"

    common = ["--database-url", url, "--schema", schema, "--seed", "1"]
    rc_first = main([*common, "--n", "3", "--out", str(first_out)])
    assert rc_first == 0
    excluded = _case_ids(first_out)
    assert excluded, "sanity: the first sample must produce at least one candidate to exclude"

    rc_second = main([*common, "--n", "3", "--out", str(second_out), "--exclude", str(first_out)])
    assert rc_second == 0
    remaining = _case_ids(second_out)

    assert remaining, "sanity: 6 seeded alerts minus 3 excluded still leaves candidates"
    assert excluded.isdisjoint(remaining)
