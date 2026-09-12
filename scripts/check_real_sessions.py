"""`check_real_sessions.py` — the fixture-vs-real schema report (PRD §1.2, §6.1, §7.1, §10.1;
m6 task-06).

Usage (on the box, one-off, read-only, inside the deployed api container):

    docker compose run --rm api uv run python scripts/check_real_sessions.py --limit 200

Usage (locally, `DATABASE_URL` read from the environment):

    uv run python scripts/check_real_sessions.py --limit 50

Reads the newest `--limit` `alerts` rows (newest first, by `received_at`), validates each `raw`
payload through the api's own `SessionAlert`, and renders a markdown report of eventid
frequencies, per-eventid field NAMES the synthetic fixtures never used (`CowrieEvent`'s
`extra="allow"` keeps them in `model_extra` — this report is about `CowrieEvent`'s own DECLARED
field set, so a field cowrie-events.md documents upstream but `CowrieEvent` itself does not
declare, e.g. `hassh` on `cowrie.client.kex`, is correctly reported as extra here too), sessions
the shipper truncated, unclosed (idle-flushed) sessions, duration and event-count percentiles
(`evals.scoring.percentile`, nearest-rank), and the storage numbers `database.md`'s retention
table needs. Read-only — no writes, no commits. Never prints a raw payload, an attacker-controlled
value, or a label: field NAMES and counts only (PRD §10.6). A row whose `raw` fails
`SessionAlert.model_validate` (schema drift) is counted in `SessionReport.n_invalid` and never
raises — the report's job is to describe reality, including a schema-drifted row, not to crash on
it.

No `from __future__ import annotations` here (matches `scripts/seed_dev.py`'s own note, copied
verbatim): the pinned `tests/test_check_real_sessions.py` loads this file via
`importlib.util.spec_from_file_location` without registering it in `sys.modules`, and
`dataclasses` (`SessionReport` below) needs to resolve postponed (string) annotations through
`sys.modules[cls.__module__]`, which is `None` under that loading path — a stdlib `AttributeError`
at import time. Python 3.12 evaluates `X | Y` and `dict[str, X]` natively, so nothing here
actually depends on postponed evaluation.
"""

import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.cli import Parser, UsageError, fail
from core.db import make_engine, make_session_factory
from core.models import AlertRow
from core.schemas.alert import SessionAlert
from evals.scoring import percentile

# The 11 ids in `.claude/skills/cowrie-fixture/references/cowrie-events.md`'s per-event table.
SUMMARIZED_EVENTIDS: frozenset[str] = frozenset(
    {
        "cowrie.session.connect",
        "cowrie.client.version",
        "cowrie.client.kex",
        "cowrie.login.failed",
        "cowrie.login.success",
        "cowrie.command.input",
        "cowrie.command.failed",
        "cowrie.session.file_download",
        "cowrie.session.file_upload",
        "cowrie.session.closed",
        "cowrie.log.closed",
    }
)

# The 7 ids in cowrie-events.md's "Other events Cowrie emits" list.
OTHER_KNOWN_EVENTIDS: frozenset[str] = frozenset(
    {
        "cowrie.client.size",
        "cowrie.client.var",
        "cowrie.client.fingerprint",
        "cowrie.direct-tcpip.request",
        "cowrie.direct-tcpip.data",
        "cowrie.session.params",
        "cowrie.command.success",
    }
)


@dataclass(frozen=True)
class SessionReport:
    """The fixture-vs-real schema report's data: field names and counts only, never a value.

    `n_alerts` counts every row `collect()` read (valid or not); `n_invalid` is the subset whose
    `raw` failed `SessionAlert.model_validate`. Percentiles and per-eventid stats are computed
    only over the rows that validated. `duration_ms_p50`/`p95` are `None` when no sampled session
    ever closed.
    """

    n_alerts: int
    n_invalid: int
    eventid_counts: dict[str, int]
    unknown_eventids: dict[str, int]
    extra_fields_by_eventid: dict[str, list[str]]
    n_truncated: int
    truncated_events_total: int
    n_unclosed: int
    events_per_session_p50: float
    events_per_session_p95: float
    duration_ms_p50: float | None
    duration_ms_p95: float | None
    raw_bytes_mean: float
    raw_bytes_total: int
    by_status: dict[str, int]


async def collect(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int
) -> SessionReport:
    """Read the newest `limit` `alerts` rows and summarize their schema shape (read-only).

    Never raises on a row whose `raw` fails `SessionAlert.model_validate` — such a row is counted
    in `n_invalid` and skipped for every per-event/per-session statistic (it has no valid events
    to summarize).

    Args:
        session_factory: An `async_sessionmaker` bound to the database to read.
        limit: The number of newest (by `received_at desc`) `alerts` rows to sample.

    Returns:
        The `SessionReport` summarizing the sample.
    """
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AlertRow).order_by(AlertRow.received_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    n_alerts = len(rows)
    n_invalid = 0
    eventid_counts: dict[str, int] = {}
    unknown_eventids: dict[str, int] = {}
    extra_field_names: dict[str, set[str]] = {}
    n_truncated = 0
    truncated_events_total = 0
    n_unclosed = 0
    events_per_session: list[float] = []
    closed_durations_ms: list[float] = []
    raw_bytes_total = 0
    by_status: dict[str, int] = {}

    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        raw_bytes_total += len(json.dumps(row.raw))

        try:
            alert = SessionAlert.model_validate(row.raw)
        except ValidationError:
            n_invalid += 1
            continue

        events_per_session.append(float(len(alert.events)))

        for event in alert.events:
            eventid_counts[event.eventid] = eventid_counts.get(event.eventid, 0) + 1
            if event.eventid not in SUMMARIZED_EVENTIDS and event.eventid not in (
                OTHER_KNOWN_EVENTIDS
            ):
                unknown_eventids[event.eventid] = unknown_eventids.get(event.eventid, 0) + 1
            extra = event.model_extra or {}
            if extra:
                extra_field_names.setdefault(event.eventid, set()).update(extra.keys())

        shipper = (alert.model_extra or {}).get("shipper")
        if isinstance(shipper, dict) and "truncated_events" in shipper:
            n_truncated += 1
            truncated_value = shipper["truncated_events"]
            if isinstance(truncated_value, int):
                truncated_events_total += truncated_value

        if alert.close_time is None:
            n_unclosed += 1
        elif alert.duration_ms is not None:
            closed_durations_ms.append(float(alert.duration_ms))

    return SessionReport(
        n_alerts=n_alerts,
        n_invalid=n_invalid,
        eventid_counts=eventid_counts,
        unknown_eventids=unknown_eventids,
        extra_fields_by_eventid={k: sorted(v) for k, v in extra_field_names.items()},
        n_truncated=n_truncated,
        truncated_events_total=truncated_events_total,
        n_unclosed=n_unclosed,
        events_per_session_p50=percentile(events_per_session, 50),
        events_per_session_p95=percentile(events_per_session, 95),
        duration_ms_p50=percentile(closed_durations_ms, 50) if closed_durations_ms else None,
        duration_ms_p95=percentile(closed_durations_ms, 95) if closed_durations_ms else None,
        raw_bytes_mean=(raw_bytes_total / n_alerts) if n_alerts else 0.0,
        raw_bytes_total=raw_bytes_total,
        by_status=by_status,
    )


def _fmt_optional(value: float | None) -> str:
    """Render `value`, or `"n/a"` when it is `None` (no closed session in the sample)."""
    return "n/a" if value is None else str(value)


def _suggested_followups(report: SessionReport) -> list[str]:
    """Build the report's mechanical "Suggested follow-ups" bullets (Interfaces, brief lines
    86-91): one per unknown eventid, one per extra field on a SUMMARIZED eventid, one if any row
    was invalid, one if any session was truncated. No judgment — every bullet is a direct function
    of the report's own counts.
    """
    items: list[str] = []

    for eventid in sorted(report.unknown_eventids):
        count = report.unknown_eventids[eventid]
        items.append(
            f"`{eventid}` seen {count} times — add to cowrie-events.md's 'other events' list"
        )

    for eventid in sorted(report.extra_fields_by_eventid):
        if eventid not in SUMMARIZED_EVENTIDS:
            continue
        for field in report.extra_fields_by_eventid[eventid]:
            items.append(
                f"`{eventid}.{field}` seen — consider a synthetic fixture carrying it "
                "(v2 fixtures, M7) — NEVER edit an existing v1 fixture"
            )

    if report.n_invalid > 0:
        items.append(
            f"{report.n_invalid} payload(s) failed SessionAlert — inspect on the box: "
            "`select id, received_at from alerts where …` — the report never prints raw"
        )

    if report.n_truncated > 0:
        items.append(
            f"{report.n_truncated} session(s) were truncated by the shipper "
            f"({report.truncated_events_total} events dropped in total) — review the shipper's "
            "spool/truncation settings"
        )

    return items


def render(report: SessionReport) -> str:
    """Render `report` as markdown: a summary table, then one table per section.

    Field NAMES and counts only — never an attacker-controlled value (username, password,
    command, URL, IP, banner). The last section is "Suggested follow-ups" (mechanical, see
    `_suggested_followups`).

    Args:
        report: The `SessionReport` to render.

    Returns:
        The markdown report, ending in a trailing newline.
    """
    lines: list[str] = [
        "# check_real_sessions report",
        "",
        "Field names and counts only — never an attacker-controlled value or a label (PRD §10.6).",
        "",
        "## Summary",
        "| metric | value |",
        "|---|---|",
        f"| n_alerts | {report.n_alerts} |",
        f"| n_invalid | {report.n_invalid} |",
        f"| n_truncated | {report.n_truncated} |",
        f"| truncated_events_total | {report.truncated_events_total} |",
        f"| n_unclosed | {report.n_unclosed} |",
        f"| events_per_session_p50 | {report.events_per_session_p50} |",
        f"| events_per_session_p95 | {report.events_per_session_p95} |",
        f"| duration_ms_p50 | {_fmt_optional(report.duration_ms_p50)} |",
        f"| duration_ms_p95 | {_fmt_optional(report.duration_ms_p95)} |",
        f"| raw_bytes_mean | {report.raw_bytes_mean:.1f} |",
        f"| raw_bytes_total | {report.raw_bytes_total} |",
        "",
        "## Eventid counts",
        "| eventid | count |",
        "|---|---|",
    ]
    for eventid in sorted(report.eventid_counts):
        lines.append(f"| {eventid} | {report.eventid_counts[eventid]} |")

    lines.append("")
    lines.append("## Unknown eventids")
    if report.unknown_eventids:
        lines.append("| eventid | count |")
        lines.append("|---|---|")
        for eventid in sorted(report.unknown_eventids):
            lines.append(f"| {eventid} | {report.unknown_eventids[eventid]} |")
    else:
        lines.append("None observed.")

    lines.append("")
    lines.append("## Extra fields by eventid (names only, never values)")
    if report.extra_fields_by_eventid:
        lines.append("| eventid | fields |")
        lines.append("|---|---|")
        for eventid in sorted(report.extra_fields_by_eventid):
            fields = ", ".join(report.extra_fields_by_eventid[eventid])
            lines.append(f"| {eventid} | {fields} |")
    else:
        lines.append("None observed.")

    lines.append("")
    lines.append("## Status breakdown")
    lines.append("| status | count |")
    lines.append("|---|---|")
    for status in sorted(report.by_status):
        lines.append(f"| {status} | {report.by_status[status]} |")

    lines.append("")
    lines.append("## Suggested follow-ups")
    followups = _suggested_followups(report)
    if followups:
        lines.extend(f"- {item}" for item in followups)
    else:
        lines.append("- None.")

    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the report against a database and print it to stdout.

    Builds its own engine/session factory exactly as `scripts/seed_dev.py` does, and disposes the
    engine before returning.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.

    Returns:
        `0` on success (the markdown report printed to stdout); `1` via
        `core.cli.fail("config_error", ...)` on a usage error or a missing database URL — never
        prints a URL.
    """
    parser = Parser(prog="check_real_sessions.py")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=None)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    database_url = args.database_url
    if database_url is None:
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL") or ""
    if not database_url:
        return fail("config_error", "no database URL (pass --database-url or set DATABASE_URL)")

    async def _run() -> SessionReport:
        engine = make_engine(database_url, schema=args.schema)
        try:
            factory = make_session_factory(engine)
            return await collect(factory, limit=args.limit)
        finally:
            await engine.dispose()

    report = asyncio.run(_run())
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
