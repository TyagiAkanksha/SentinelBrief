"""Extends `scripts/check_real_sessions.py`'s output-hygiene coverage beyond the pinned
`tests/test_check_real_sessions.py::test_render_never_contains_attacker_strings`'s three classes
(m6 task-06 fix-1 PC2/I3, I5, M3, M6).

The pinned file's `_ATTACKER_STRINGS` covers a username, a command, and a URL. The review found
the milestone's own Global Constraint names seven forbidden value classes plus a handful of other
per-event fields the report must never echo: `src_ip`, a password, the client banner
(`cowrie.client.version`'s `version`), the session id, `sensor`, `shasum`, `outfile`, `message` —
plus an extra event field's VALUE and a top-level envelope extra field's VALUE. This file seeds
one alert carrying a distinctive canary in every one of those, and asserts none of them survives
into `render()`'s markdown or `main()`'s captured stdout/stderr.

Also covers I5 (an unreachable database must fail cleanly, never a traceback or a password) and
M6 (`_safe_name()` truncates and sanitizes an oversized/hostile field name) and M3 (`raw_bytes_
total` counts an invalid row too). Seeding goes through `tests.helpers.load_alert` +
`model_dump` + edit + `SessionAlert.model_validate`, mirroring the pinned file's own
`_load_and_edit` shape (test files never import from each other, so this is a small, deliberate
duplication) — except the one deliberately invalid raw-SQL row (M3), which bypasses `insert_alert`
on purpose, exactly like the pinned file's own `_insert_invalid_raw_row`.

m6 task-06 fix-2 adds three more pins to this file:
- **N2**: `_first_loc_key` (the one function that touches a `ValidationError`) has a negative
  pin — a row whose `events[0].timestamp` is an attacker-controlled string must surface only
  `events.*.timestamp` (the declared field path, index normalized to `*`), never the payload or
  pydantic's own `msg` (which embeds the payload via `input_value=...`).
- **N4**: the `--limit` `1..5000` clamp is no longer silent — `main()` prints one `note: --limit
  clamped to <n>` stderr line when it actually fires; pinned here for the CLI. The two Summary
  captions (M1/M2) are pinned in `tests/test_check_real_sessions_followups.py` instead (built by
  hand, no DB needed).
- **N5**: the invalid-ids bullet caps at the first 20 ids, then `… and N more` — never an
  unbounded line of UUIDs for a broadly-drifted sample.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db import make_engine, make_session_factory
from core.schemas.alert import SessionAlert
from core.services.alerts import insert_alert
from tests.helpers import load_alert

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_real_sessions.py"

_CANARY: dict[str, str] = {
    "username": "CANARY-USERNAME-HY1",
    "password": "CANARY-PASSWORD-HY2",
    "command": "CANARY-COMMAND-HY3 --flag",
    "url": "http://canary-hy4.invalid/x.sh",
    "src_ip": "203.0.113.222",
    "banner": "SSH-2.0-CANARY-HY5",
    "session_id": "canary-hygiene-session-hy6",
    "sensor": "canary-sensor-hy7",
    "shasum": "deadbeefcanaryhy8",
    "outfile": "/tmp/canary-hy9.bin",
    "message": "CANARY-MESSAGE-HYA custom cowrie message",
}
_EXTRA_FIELD_VALUE = "SECRET-VALUE"
_ENVELOPE_EXTRA_VALUE = "ENV-SECRET"

# M6: a 200-char extra-field key with a pipe and a newline inside the first 64 characters, so
# `_safe_name()`'s truncate-then-substitute order is exercised for both operations at once.
_MALICIOUS_KEY = "A" * 30 + "|" + "B" * 30 + "\n" + "C" * 138
_EXPECTED_SAFE_KEY = "A" * 30 + "?" + "B" * 30 + "?" + "CC"
assert len(_MALICIOUS_KEY) == 200
assert len(_EXPECTED_SAFE_KEY) == 64


def _load_check_real_sessions() -> ModuleType:
    """Load `scripts/check_real_sessions.py` as a standalone module — mirrors the pinned file's
    own `_load_check_real_sessions` (test files never import from each other)."""
    spec = importlib.util.spec_from_file_location("check_real_sessions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_and_edit(
    name: str, *, session_id: str, edit: Callable[[dict[str, Any]], None]
) -> SessionAlert:
    """Load `fixtures/alerts/<name>.json`, apply `edit` in place to its `model_dump(mode="json")`
    dict, and re-validate through `SessionAlert` — mirrors the pinned file's own helper."""
    data = load_alert(name, session_id=session_id).model_dump(mode="json")
    edit(data)
    return SessionAlert.model_validate(data)


def _canary_alert() -> SessionAlert:
    """`fixtures/alerts/alert1.json` with every forbidden value class (PC2/I3) replaced by a
    distinctive canary, plus an extra event field and a top-level envelope extra field.
    """

    def _edit(data: dict[str, Any]) -> None:
        session_id = _CANARY["session_id"]
        sensor = _CANARY["sensor"]
        src_ip = _CANARY["src_ip"]
        data["session_id"] = session_id
        data["sensor"] = sensor
        data["src_ip"] = src_ip
        data["envelope_extra"] = _ENVELOPE_EXTRA_VALUE

        connect_ts = datetime.fromisoformat(data["events"][0]["timestamp"])

        def _base(eventid: str, offset_ms: int) -> dict[str, Any]:
            return {
                "eventid": eventid,
                "timestamp": (connect_ts + timedelta(milliseconds=offset_ms)).isoformat(),
                "session": session_id,
                "src_ip": src_ip,
                "sensor": sensor,
            }

        data["events"] = [
            {**_base("cowrie.session.connect", 0), "message": _CANARY["message"]},
            {**_base("cowrie.client.version", 100), "version": _CANARY["banner"]},
            {
                **_base("cowrie.login.failed", 200),
                "username": _CANARY["username"],
                "password": _CANARY["password"],
                "banana": _EXTRA_FIELD_VALUE,
            },
            {**_base("cowrie.command.input", 300), "input": _CANARY["command"]},
            {
                **_base("cowrie.session.file_download", 400),
                "url": _CANARY["url"],
                "outfile": _CANARY["outfile"],
                "shasum": _CANARY["shasum"],
            },
            {**_base("cowrie.session.closed", 500), "duration_ms": 500},
        ]

    return _load_and_edit("alert1", session_id=_CANARY["session_id"], edit=_edit)


def _malicious_key_alert() -> SessionAlert:
    """`fixtures/alerts/alert1.json` with one event carrying a 200-char extra-field key (`|` and
    a newline inside it) — M6's sanitization pin.
    """

    def _edit(data: dict[str, Any]) -> None:
        data["events"][0][_MALICIOUS_KEY] = "irrelevant-value"

    return _load_and_edit("alert1", session_id="canary-malicious-key-session", edit=_edit)


def _insert_invalid_raw_row(url: str, schema: str, *, index: int = 0) -> None:
    """Insert one `alerts` row whose `raw` cannot possibly have passed `SessionAlert.model_
    validate` on ingest — a raw SQL `insert` bypassing `insert_alert` on purpose, mirroring the
    pinned file's own `_insert_invalid_raw_row` (M3: `raw_bytes_total` must still count it).
    `index` distinguishes multiple invalid rows in the same schema (N5: `fingerprint` is UNIQUE).
    """
    import psycopg

    raw = json.dumps(
        {"source": "cowrie", "session_id": f"invalid-row-m3-{index}", "note": "no events"}
    )
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".alerts (fingerprint, source, event_time, raw, status) '
            f"VALUES (%s, %s, now(), %s::jsonb, %s)",
            (f"invalid-row-m3-fingerprint-{index}", "cowrie", raw, "pending"),
        )


_CANARY_TIMESTAMP_PAYLOAD = "ATTACKER-CONTROLLED-TIMESTAMP-PAYLOAD"


def _insert_invalid_timestamp_row(url: str, schema: str) -> None:
    """Insert one `alerts` row that looks like a real session except `events[0].timestamp` is an
    attacker-controlled, unparseable string — the ONE way to make pydantic's `ValidationError`
    carry attacker text (in `input`/`msg`) on a field `_first_loc_key` must never surface (N2).
    Raw SQL, bypassing `insert_alert` on purpose, exactly like `_insert_invalid_raw_row` above.
    """
    import psycopg

    raw = json.dumps(
        {
            "source": "cowrie",
            "session_id": "n2-invalid-timestamp-session",
            "src_ip": "203.0.113.9",
            "sensor": "hp-n2-01",
            "events": [
                {
                    "eventid": "cowrie.session.connect",
                    "timestamp": _CANARY_TIMESTAMP_PAYLOAD,
                    "session": "n2-invalid-timestamp-session",
                    "src_ip": "203.0.113.9",
                    "sensor": "hp-n2-01",
                }
            ],
        }
    )
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".alerts (fingerprint, source, event_time, raw, status) '
            f"VALUES (%s, %s, now(), %s::jsonb, %s)",
            ("invalid-timestamp-fingerprint", "cowrie", raw, "pending"),
        )


# --- PC2 / I3: the full canary set must never survive into render() -----------------------------


async def test_render_never_leaks_the_full_canary_set(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """None of the seven forbidden value classes (username, password, command, URL, src_ip,
    banner, session id) plus sensor/shasum/outfile/message, nor an extra event field's value, nor
    a top-level envelope extra field's value, ever appears in `render()`'s markdown.
    """
    module = _load_check_real_sessions()
    canary = _canary_alert()

    async with db_session_factory() as session:
        await insert_alert(session, canary)
        await session.commit()

    report = await module.collect(db_session_factory, limit=10)
    rendered = module.render(report)

    for value in _CANARY.values():
        assert value not in rendered
    assert _EXTRA_FIELD_VALUE not in rendered
    assert _ENVELOPE_EXTRA_VALUE not in rendered
    # the envelope extra field NAME is expected to appear (names only, never values):
    assert "envelope_extra" in rendered


def test_main_never_leaks_the_full_canary_set(
    tmp_schema: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same canary set, exercised through `main()`'s stdout/stderr (capsys) rather than a
    direct `render()` call — the mutation self-check (a) target: keying `by_status` on
    `raw["src_ip"]` (or any attacker-controlled field) makes this test fail.
    """
    url, schema = tmp_schema
    module = _load_check_real_sessions()
    canary = _canary_alert()

    async def _seed() -> None:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                await insert_alert(session, canary)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    rc = module.main(["--database-url", url, "--schema", schema, "--limit", "10"])

    captured = capsys.readouterr()
    assert rc == 0
    for value in _CANARY.values():
        assert value not in captured.out
        assert value not in captured.err
    assert _EXTRA_FIELD_VALUE not in captured.out
    assert _ENVELOPE_EXTRA_VALUE not in captured.out
    assert captured.err == ""


# --- M6: oversized/hostile field names are sanitized and truncated ------------------------------


async def test_extra_field_key_is_sanitized_and_truncated(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A 200-char extra-field key containing `|` and a newline renders as the 64-char, `_safe_
    name()`-sanitized string — never the raw key.
    """
    module = _load_check_real_sessions()
    malicious = _malicious_key_alert()

    async with db_session_factory() as session:
        await insert_alert(session, malicious)
        await session.commit()

    report = await module.collect(db_session_factory, limit=10)
    rendered = module.render(report)

    assert _EXPECTED_SAFE_KEY in rendered
    assert _MALICIOUS_KEY not in rendered
    assert ("A" * 30 + "|" + "B") not in rendered
    assert "\n" + "C" not in rendered


# --- M3: raw_bytes_total counts an invalid row too -----------------------------------------------


def test_raw_bytes_total_counts_the_invalid_row_too(tmp_schema: tuple[str, str]) -> None:
    """A sample whose only row is invalid must still report a positive `raw_bytes_total`
    (mutation self-check target: moving the `len(json.dumps(row.raw))` accumulation below the
    `except ValidationError` block makes this test fail).
    """
    url, schema = tmp_schema
    module = _load_check_real_sessions()
    _insert_invalid_raw_row(url, schema)

    async def _collect() -> Any:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            return await module.collect(factory, limit=5)
        finally:
            await engine.dispose()

    report = asyncio.run(_collect())
    assert report.n_invalid == 1
    assert report.raw_bytes_total > 0


# --- I5: an unreachable database fails cleanly, never a traceback or a password -----------------


def test_main_exits_1_on_unreachable_database_without_leaking_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unreachable `--database-url` (port 1 refuses immediately, never hangs — matches
    `tests/test_seed_dev.py`'s own `_UNREACHABLE_DATABASE_URL` convention) must exit `1` through
    `core.cli.fail("database_error", ...)`: one stderr line, no traceback, no password.
    """
    module = _load_check_real_sessions()
    unreachable = "postgresql://sentinel:SUPERSECRETPW@127.0.0.1:1/nope"

    rc = module.main(["--database-url", unreachable, "--limit", "5"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "SUPERSECRETPW" not in captured.err
    assert "Traceback" not in captured.err
    assert captured.err.count("\n") == 1
    assert "error: database_error:" in captured.err


# --- N2: _first_loc_key never surfaces the ValidationError's attacker-controlled input/msg ------


def test_invalid_row_timestamp_canary_never_leaks_and_loc_normalizes_to_star(
    tmp_schema: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A row whose `events[0].timestamp` is attacker-controlled must surface only the declared
    field path `events.*.timestamp` in the "Suggested follow-ups" bullet — never the payload
    itself and never pydantic's own `msg` (which embeds the payload via `input_value=...`, hence
    the "Input should be" check). Mutation self-check (a) target: `_first_loc_key` returning
    `err["input"]`/`err["msg"]` instead of `err["loc"]` makes this test fail on both assertions.
    """
    url, schema = tmp_schema
    module = _load_check_real_sessions()
    _insert_invalid_timestamp_row(url, schema)

    async def _collect() -> Any:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            return await module.collect(factory, limit=5)
        finally:
            await engine.dispose()

    report = asyncio.run(_collect())
    assert report.n_invalid == 1
    assert report.invalid_locs == {"events.*.timestamp": 1}
    rendered = module.render(report)

    assert "events.*.timestamp" in rendered
    assert _CANARY_TIMESTAMP_PAYLOAD not in rendered
    assert "Input should be" not in rendered

    rc = module.main(["--database-url", url, "--schema", schema, "--limit", "5"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "events.*.timestamp" in captured.out
    assert _CANARY_TIMESTAMP_PAYLOAD not in captured.out
    assert "Input should be" not in captured.out
    assert captured.err == ""


# --- N4: the --limit clamp is no longer silent ---------------------------------------------------


def test_main_prints_a_note_when_limit_is_clamped(
    tmp_schema: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--limit 10000` is clamped to `5000` (M8) and `main()` must say so on stderr — an owner who
    fat-fingers `--limit` at T+48h must not silently get a smaller sample than they asked for.
    A `--limit` within bounds (the other tests in this file) prints nothing extra, so
    `captured.err == ""` there still holds.
    """
    url, schema = tmp_schema
    module = _load_check_real_sessions()

    rc = module.main(["--database-url", url, "--schema", schema, "--limit", "10000"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == "note: --limit clamped to 5000\n"


# --- N5: the invalid-ids bullet is capped, never an unbounded line of UUIDs ---------------------


def test_invalid_ids_bullet_capped_at_20_with_suffix(tmp_schema: tuple[str, str]) -> None:
    """25 invalid rows must render at most 20 ids in the "Suggested follow-ups" bullet, followed
    by `… and 5 more` — `invalid_alert_ids` itself stays the full, uncapped list (the cap is a
    presentation concern in `_suggested_followups`, not a data-loss one).
    """
    url, schema = tmp_schema
    module = _load_check_real_sessions()
    for i in range(25):
        _insert_invalid_raw_row(url, schema, index=i)

    async def _collect() -> Any:
        engine = make_engine(url, schema=schema)
        try:
            factory = make_session_factory(engine)
            return await module.collect(factory, limit=30)
        finally:
            await engine.dispose()

    report = asyncio.run(_collect())
    assert report.n_invalid == 25
    assert len(report.invalid_alert_ids) == 25

    rendered = module.render(report)

    expected_prefix = ", ".join(report.invalid_alert_ids[:20])
    assert expected_prefix in rendered
    assert "… and 5 more" in rendered
    assert report.invalid_alert_ids[20] not in rendered
