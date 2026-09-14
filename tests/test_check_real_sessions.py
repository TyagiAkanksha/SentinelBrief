"""Pins `scripts/check_real_sessions.py`'s fixture-vs-real schema report (PRD §1.2, §6.1, §7.1,
§10.1; m6 task-06).

The script is the owner's one-off, read-only report: it reads the newest N `alerts` rows,
validates each `raw` payload through the api's own `SessionAlert`, and renders a markdown report
of eventid frequencies, per-eventid field NAMES the synthetic fixtures never used, truncated and
unclosed sessions, duration/event-count percentiles, and the storage numbers `database.md`'s
retention table needs — never an attacker string, never a value, never a label.

`scripts/` has no `__init__.py`, so the module is loaded via `importlib.util.spec_from_file_
location` (`tests/test_seed_dev.py`'s `_load_seed_dev` shape, copied here — test files never
import from each other). Every test below therefore errors at fixture setup (or, for
`test_main_exit_1_without_database_url`, at its own direct load call) with a `FileNotFoundError`
until the implementer lands `scripts/check_real_sessions.py` (RED).

Seeding always goes through the production writers `tests.helpers.seed_alert` / `load_alert` /
`insert_alert` — never a hand-rolled `session.add(...)` — except the ONE deliberately invalid-raw
row `test_invalid_raw_is_counted_not_raised` builds with a raw SQL `insert` bypassing
`insert_alert` on purpose (its own docstring explains why): every other hand-edited alert here is
built by loading a real fixture, `model_dump`-ing it to a dict, editing that dict, and
re-validating through `SessionAlert` (`_load_and_edit` below), never a hand-rolled full payload.

Two DB access shapes coexist, mirroring `tests/test_seed_dev.py`:
- Tests that call `collect()` directly are `async def` and request the async `db_session_factory`
  fixture (`tests/conftest.py`).
- Tests that call `main()` are plain `def` and request the sync `tmp_schema` fixture instead —
  `main()` runs its own `asyncio.run`, which cannot nest inside pytest-asyncio's already-running
  loop — seeding data for those (when needed) through a local, non-nested `asyncio.run(...)` call
  that builds and disposes its own engine, exactly as `scripts/seed_dev.py`'s own `seed()` does.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db import make_engine, make_session_factory
from core.models import AlertRow
from core.schemas.alert import SessionAlert
from core.services.alerts import insert_alert
from evals.scoring import percentile
from tests.helpers import load_alert, seed_alert

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_real_sessions.py"

_FIXTURE_NAMES: tuple[str, ...] = ("alert1", "alert2", "alert3", "alert4", "alert5")

# From the seeded fixture payloads (task table row "negative pin"): a username (alert3's
# brute-force list), a shell command (alert4), and a download URL (alert5) — none of them may
# ever appear in `render()`'s output, which carries only counts, field NAMES, and percentiles.
_ATTACKER_STRINGS: tuple[str, ...] = (
    "sgp01admin",
    "cat /etc/passwd",
    "http://203.0.113.200/x.sh",
)

# A syntactically-plausible URL that is never actually dialed (this test never reaches the
# database): proves `main`'s "no database URL" failure never echoes whatever URL it did (or did
# not) see, even if the module had captured one at import time instead of reading it in `main()`.
_CANARY_DATABASE_URL = "postgresql://canary-user:canary-pass@canary-host.invalid/canary-db"


def _load_check_real_sessions() -> ModuleType:
    """Load `scripts/check_real_sessions.py` as a standalone module (no package `__init__.py`
    exists) — mirrors `tests/test_seed_dev.py`'s `_load_seed_dev`."""
    spec = importlib.util.spec_from_file_location("check_real_sessions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check_real_sessions() -> ModuleType:
    """The freshly (re)loaded `scripts/check_real_sessions.py` module, one load per test."""
    return _load_check_real_sessions()


def _load_and_edit(
    name: str, *, session_id: str, edit: Callable[[dict[str, Any]], None]
) -> SessionAlert:
    """Load `fixtures/alerts/<name>.json`, apply `edit` in place to its `model_dump(mode="json")`
    dict, and re-validate through `SessionAlert` — the shared shape behind every hand-edited alert
    in this module (never a hand-rolled full payload); the sole exception, a raw-SQL-inserted
    invalid row, is `_insert_invalid_raw_row` below, which does not go through this helper.
    """
    data = load_alert(name, session_id=session_id).model_dump(mode="json")
    edit(data)
    return SessionAlert.model_validate(data)


def _insert_invalid_raw_row(url: str, schema: str) -> None:
    """Insert one `alerts` row whose `raw` cannot possibly have passed `SessionAlert.model_
    validate` on ingest (`raw` lacks `events` entirely) — a raw SQL `insert` bypassing
    `insert_alert` on purpose, simulating the kind of schema drift the production writer itself
    would have rejected, so it is `collect()`'s own per-row validation path under test here, not
    `insert_alert`'s. The ONE deliberately hand-rolled row in this file (task table row "invalid
    rows").
    """
    import psycopg

    raw = json.dumps({"source": "cowrie", "session_id": "invalid-row", "note": "no events key"})
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".alerts (fingerprint, source, event_time, raw, status) '
            f"VALUES (%s, %s, now(), %s::jsonb, %s)",
            ("invalid-row-fingerprint", "cowrie", raw, "pending"),
        )


def _alert_with_n_commands(i: int, *, sensor: str) -> SessionAlert:
    """`fixtures/alerts/alert1.json` with `i` extra `cowrie.command.input` events appended between
    its connect and closed events — the per-alert discriminator
    `test_limit_takes_newest_by_received_at` uses to prove `collect`'s `limit` selects the newest
    rows specifically, not merely any `limit` of them.
    """

    def _edit(data: dict[str, Any]) -> None:
        connect_ts = datetime.fromisoformat(data["events"][0]["timestamp"])
        commands = [
            {
                "eventid": "cowrie.command.input",
                "timestamp": (connect_ts + timedelta(milliseconds=100 * (j + 1))).isoformat(),
                "session": data["session_id"],
                "src_ip": data["src_ip"],
                "sensor": sensor,
                "input": f"echo marker-{i}-{j}",
            }
            for j in range(i)
        ]
        data["sensor"] = sensor
        data["events"] = [data["events"][0], *commands, data["events"][-1]]

    return _load_and_edit("alert1", session_id=f"limit-test-{i}", edit=_edit)


# --- counts + known ids --------------------------------------------------------------------------


async def test_collect_counts_eventids_and_flags_unknown(
    db_session_factory: async_sessionmaker[AsyncSession], check_real_sessions: ModuleType
) -> None:
    """Pins `SUMMARIZED_EVENTIDS`/`OTHER_KNOWN_EVENTIDS` (Interfaces lines 65-66: 11 and 7 ids
    respectively, from `.claude/skills/cowrie-fixture/references/cowrie-events.md`'s per-event
    table and its "Other events" list) and `SessionReport.eventid_counts`/`unknown_eventids`
    (line 71). The five `fixtures/alerts/*.json` sessions each contribute exactly one
    `cowrie.session.connect`; a sixth, hand-edited alert (`alert1` plus one appended
    `cowrie.made.up` event, via `load_alert` + `model_dump` + edit + `insert_alert`, per the test
    table row) contributes both a seventh connect and an eventid outside both frozensets, which
    the report must flag in `unknown_eventids` rather than silently fold into `eventid_counts`
    alone. Test table row "counts + known ids".
    """

    def _add_unknown_event(data: dict[str, Any]) -> None:
        data["events"].append(
            {
                "eventid": "cowrie.made.up",
                "timestamp": "2026-09-06T03:12:05.500000Z",
                "session": data["session_id"],
                "src_ip": data["src_ip"],
                "sensor": data["sensor"],
            }
        )

    made_up_alert = _load_and_edit("alert1", session_id="made-up-session", edit=_add_unknown_event)

    async with db_session_factory() as session:
        for name in _FIXTURE_NAMES:
            await seed_alert(session, name)
        await insert_alert(session, made_up_alert)
        await session.commit()

    report = await check_real_sessions.collect(db_session_factory, limit=10)

    assert len(check_real_sessions.SUMMARIZED_EVENTIDS) == 11
    assert len(check_real_sessions.OTHER_KNOWN_EVENTIDS) == 7
    assert report.eventid_counts["cowrie.session.connect"] == 6
    assert report.unknown_eventids == {"cowrie.made.up": 1}


# --- extra fields -----------------------------------------------------------------------------


async def test_extra_field_names_reported_never_values(
    db_session_factory: async_sessionmaker[AsyncSession], check_real_sessions: ModuleType
) -> None:
    """Pins `extra_fields_by_eventid` (Interfaces line 72; test table row "extra fields"): field
    NAMES present in `CowrieEvent.model_extra` survive into the report, values never do. `hassh`
    on `cowrie.client.kex` is declared in cowrie-events.md's reference table but **not** on
    `CowrieEvent` (`core/schemas/alert.py`'s field list omits it) — the report is about
    `CowrieEvent`'s own declared set, so `hassh` is correctly reported as extra here too, per the
    task table row's note.
    """

    def _add_kex_and_login_failed(data: dict[str, Any]) -> None:
        sensor, session_id, src_ip = data["sensor"], data["session_id"], data["src_ip"]
        data["events"] = [
            data["events"][0],
            {
                "eventid": "cowrie.client.kex",
                "timestamp": "2026-09-06T03:12:05.200000Z",
                "session": session_id,
                "src_ip": src_ip,
                "sensor": sensor,
                "hassh": "deadbeef",
            },
            {
                "eventid": "cowrie.login.failed",
                "timestamp": "2026-09-06T03:12:05.400000Z",
                "session": session_id,
                "src_ip": src_ip,
                "sensor": sensor,
                "username": "root",
                "password": "toor",
                "banana": "SECRET-VALUE",
            },
            data["events"][-1],
        ]

    edited = _load_and_edit(
        "alert1", session_id="extra-fields-session", edit=_add_kex_and_login_failed
    )

    async with db_session_factory() as session:
        await insert_alert(session, edited)
        await session.commit()

    report = await check_real_sessions.collect(db_session_factory, limit=10)
    rendered = check_real_sessions.render(report)

    assert report.extra_fields_by_eventid["cowrie.login.failed"] == ["banana"]
    assert report.extra_fields_by_eventid["cowrie.client.kex"] == ["hassh"]
    assert "SECRET-VALUE" not in rendered


# --- truncated + unclosed -----------------------------------------------------------------------


async def test_truncated_and_unclosed_sessions_counted(
    db_session_factory: async_sessionmaker[AsyncSession], check_real_sessions: ModuleType
) -> None:
    """Pins `n_truncated`/`truncated_events_total`/`n_unclosed`/`duration_ms_p50` (Interfaces
    lines 73-76; test table row "truncated + unclosed"): one payload carries the shipper's
    `shipper.truncated_events` marker; one payload has no `cowrie.session.closed` event at all
    (idle-flushed) and so contributes no duration sample — `duration_ms_p50` must be computed over
    the closed session alone.
    """

    def _add_shipper_truncation(data: dict[str, Any]) -> None:
        data["shipper"] = {"version": "0.1.0", "truncated_events": 12}

    def _drop_closed_event(data: dict[str, Any]) -> None:
        data["events"] = [data["events"][0]]  # connect only: never closed

    truncated_alert = _load_and_edit(
        "alert1", session_id="truncated-session", edit=_add_shipper_truncation
    )
    unclosed_alert = _load_and_edit(
        "alert1", session_id="unclosed-session", edit=_drop_closed_event
    )

    async with db_session_factory() as session:
        await insert_alert(session, truncated_alert)
        await insert_alert(session, unclosed_alert)
        await session.commit()

    report = await check_real_sessions.collect(db_session_factory, limit=10)

    assert report.n_truncated == 1
    assert report.truncated_events_total == 12
    assert report.n_unclosed == 1
    assert report.duration_ms_p50 == 1868.0  # alert1's own duration_ms; the only closed session


# --- percentiles + bytes --------------------------------------------------------------------------


async def test_percentiles_and_raw_bytes(
    db_session_factory: async_sessionmaker[AsyncSession], check_real_sessions: ModuleType
) -> None:
    """Pins `events_per_session_p50`/`p95` and `raw_bytes_total` (Interfaces lines 75, 77; test
    table row "percentiles + bytes"): the percentiles are `evals.scoring.percentile`'s own
    nearest-rank value over the five fixtures' event counts; `raw_bytes_total` is computed from
    the rows read back from the database (never re-derived from the fixture files on disk).
    """
    expected_counts = [float(len(load_alert(name).events)) for name in _FIXTURE_NAMES]

    async with db_session_factory() as session:
        for name in _FIXTURE_NAMES:
            await seed_alert(session, name)
        await session.commit()

    async with db_session_factory() as session:
        stored_raws = (await session.execute(select(AlertRow.raw))).scalars().all()
    assert len(stored_raws) == 5  # sanity: the byte total below is not computed over a wrong set
    expected_raw_bytes_total = sum(len(json.dumps(raw)) for raw in stored_raws)

    report = await check_real_sessions.collect(db_session_factory, limit=10)

    assert report.events_per_session_p50 == percentile(expected_counts, 50)
    assert report.events_per_session_p95 == percentile(expected_counts, 95)
    assert report.raw_bytes_total == expected_raw_bytes_total


# --- invalid rows -----------------------------------------------------------------------------


def test_invalid_raw_is_counted_not_raised(
    tmp_schema: tuple[str, str],
    check_real_sessions: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pins `n_invalid` and `main`'s exit code over a schema-drifted row (Interfaces line 69; test
    table row "invalid rows"): a hand-inserted row whose `raw` lacks `events` entirely (raw SQL,
    bypassing `insert_alert` on purpose — `_insert_invalid_raw_row`'s docstring explains why) must
    be COUNTED by `collect()`, never raised past it, and `main()` must still exit 0 over a sample
    that includes it — the report's job is to describe reality, including one schema-drifted row,
    not to crash on it.
    """
    url, schema = tmp_schema
    _insert_invalid_raw_row(url, schema)

    async def _collect() -> Any:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            return await check_real_sessions.collect(factory, limit=5)
        finally:
            await engine.dispose()

    report = asyncio.run(_collect())
    assert report.n_alerts == 1
    assert report.n_invalid == 1

    rc = check_real_sessions.main(["--database-url", url, "--schema", schema, "--limit", "5"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""


# --- limit + order ------------------------------------------------------------------------------


async def test_limit_takes_newest_by_received_at(
    db_session_factory: async_sessionmaker[AsyncSession], check_real_sessions: ModuleType
) -> None:
    """Pins `collect`'s `limit` (Interfaces line 79; test table row "limit + order"): with 7
    alerts at distinct `received_at` timestamps, `limit=3` must select the THREE NEWEST, never
    merely any three. `SessionReport` exposes no per-alert identity, so identity is proven
    indirectly: alert `i` (`1..7`, `i=7` newest) carries `i` extra `cowrie.command.input` events
    tied to its own ordinal, so only the newest three (5, 6, 7 -> 18) can produce
    `eventid_counts["cowrie.command.input"] == 18` — the oldest three sum to 6, the middle three
    to 12, and all seven to 28. Each alert also carries a distinct marker `sensor` name
    (`limit-marker-<i>`), unused by any assertion here, purely so the seeded rows stay
    distinguishable on the box while debugging a real failure of this test.
    """
    base_received_at = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    for i in range(1, 8):
        alert = _alert_with_n_commands(i, sensor=f"limit-marker-{i}")
        async with db_session_factory() as session:
            result = await insert_alert(session, alert)
            row = await session.get(AlertRow, result.alert_id)
            assert row is not None
            row.received_at = base_received_at + timedelta(minutes=i)
            await session.flush()
            await session.commit()

    report = await check_real_sessions.collect(db_session_factory, limit=3)

    assert report.n_alerts == 3
    assert report.eventid_counts.get("cowrie.command.input", 0) == 18  # ordinals 5 + 6 + 7


# --- CLI ------------------------------------------------------------------------------------------


def test_main_prints_markdown_and_exits_zero(
    tmp_schema: tuple[str, str],
    check_real_sessions: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pins the CLI's happy path (Interfaces lines 81-84; test table row "CLI"): `main` builds its
    own engine/session factory exactly as `scripts/seed_dev.py` does, over the throwaway schema's
    URL and name (`tests/test_seed_dev.py`'s own convention), renders the markdown report to
    stdout, and exits 0. `| eventid |` pins the per-eventid table's header; `Suggested follow-ups`
    pins the report's mandated last section.
    """
    url, schema = tmp_schema

    async def _seed() -> None:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                for name in _FIXTURE_NAMES:
                    await seed_alert(session, name)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    rc = check_real_sessions.main(["--database-url", url, "--schema", schema, "--limit", "5"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert "| eventid |" in captured.out
    assert "Suggested follow-ups" in captured.out


def test_main_exit_1_without_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pins the CLI's `config_error` exit and its exact `core.cli.fail` message (Interfaces
    line 84: `fail("config_error", "no database URL (pass --database-url or set DATABASE_URL)")`
    — never a URL; test table row "CLI"). Sets both env vars to a canary value BEFORE (re)loading
    the module — so a hypothetical bug that captured a URL at import time instead of reading it
    inside `main()` would be caught — then deletes both and calls `main([])` with no
    `--database-url` flag; the canary must never appear in stdout or stderr.
    """
    monkeypatch.setenv("DATABASE_URL", _CANARY_DATABASE_URL)
    monkeypatch.setenv("TEST_DATABASE_URL", _CANARY_DATABASE_URL)
    module = _load_check_real_sessions()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    rc = module.main([])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == (
        "error: config_error: no database URL (pass --database-url or set DATABASE_URL)\n"
    )
    assert _CANARY_DATABASE_URL not in captured.out
    assert _CANARY_DATABASE_URL not in captured.err


# --- negative pin (rule 1) -----------------------------------------------------------------------


async def test_render_never_contains_attacker_strings(
    db_session_factory: async_sessionmaker[AsyncSession], check_real_sessions: ModuleType
) -> None:
    """Rule-1 negative pin (PRD §10.6, CONVENTIONS.md §10; test table row "negative pin"): every
    attacker-controlled string in the five seeded fixtures — a username, a shell command, and a
    download URL (`_ATTACKER_STRINGS`) — must be wholly absent from `render()`'s markdown, which
    carries only counts, field NAMES, and percentiles.
    """
    async with db_session_factory() as session:
        for name in _FIXTURE_NAMES:
            await seed_alert(session, name)
        await session.commit()

    report = await check_real_sessions.collect(db_session_factory, limit=10)
    rendered = check_real_sessions.render(report)

    assert all(s not in rendered for s in _ATTACKER_STRINGS)
