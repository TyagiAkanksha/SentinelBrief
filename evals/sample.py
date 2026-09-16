"""`evals.sample`: the stratified, verdict-blind v2 candidate export (PRD §7.1, §10.6; m7 task-01).

`python -m evals.sample --database-url … [--schema …] --n 240 --seed 20260914 --out <path>
[--since <date>] [--exclude <path>]` reads the production database (read-only; the same
`--database-url`/`--schema` seam as `scripts/check_real_sessions.py`), joins each `alerts` row
that has finished triage (`status in ("triaged", "failed")`) with its LATEST verdict, stratifies
by the cheap verdict's category (or `"injection-candidate"` when the session's usernames/commands
carry instruction-like text, which takes priority over the category; or `"unverdicted"` when no
verdict exists at all), and writes a candidate JSONL that carries the raw `SessionAlert`, a stable
`case_id` (`alert.fingerprint()`, the repo-wide `GoldenCase.case_id` convention) and sampling
provenance — and NO verdict field of any kind, so the human labeler is never anchored by the
model's own opinion (PRD §13).

This module's query is its own (M6 task-06 review PC4): `scripts/check_real_sessions.py`'s report
query is read-only diagnostics and is never reused as a sampling frame. This module never imports
the golden-set label type and never assigns `labeled_by` — nothing here can mint a v2 label; that
is the label tool's job alone (`evals/label_tool.py::prompt_label`, PRD §13).

`Candidate`/`STRATA_CATEGORIES`/`stratum_id` are re-exported here from the DB-free
`evals.candidates` (m7 task-01 fix-1 ruling R14) so every existing importer of
`evals.sample.Candidate` etc. keeps working; the label tool imports them from `evals.candidates`
directly instead, so it never needs this module's `sqlalchemy`/`core.db`/`core.models` imports.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from core.cli import Parser, UsageError, fail
from core.config import Settings
from core.db import make_engine, make_session_factory
from core.models import AlertRow, VerdictRow
from core.schemas.alert import SessionAlert
from evals.candidates import STRATA_CATEGORIES, Candidate, stratum_id

INJECTION_HINT = re.compile(
    r"ignore (all |previous |prior )?instructions|system prompt|as an ai"
    r"|severity ?[:=] ?[1-5]|rate (this|it) (as )?(low|benign|1)",
    re.I,
)
"""Ruling R11: no bare `assistant` (matched too much innocuous text, e.g. `assistant_manager`);
`severity` now requires a literal `:` or `=` before the digit (`severity 3` alone no longer
matches) — a hint for the sampling stratum only, never a label; the human decides the `injection`
tag (`docs/labeling-guide.md`)."""


def _matches_injection_hint(alert: SessionAlert) -> bool:
    """Whether any event's `username`/`input` carries instruction-like text (PRD §10.6)."""
    for event in alert.events:
        if event.username is not None and INJECTION_HINT.search(event.username):
            return True
        if event.input is not None and INJECTION_HINT.search(event.input):
            return True
    return False


def _stratum_for(alert: SessionAlert, category: str | None) -> str:
    """The sampling stratum for one alert: injection-candidate first, else category, else
    unverdicted."""
    if _matches_injection_hint(alert):
        return "injection-candidate"
    if category is not None:
        return category
    return "unverdicted"


async def _fetch_eligible(
    session_factory: async_sessionmaker[AsyncSession], *, since: datetime | None
) -> list[tuple[str, datetime, dict[str, object], str | None]]:
    """Newest-first: every finished alert LEFT JOINed with its latest verdict's category.

    A subquery picks each alert's `max(created_at)` verdict; the outer join resolves that row's
    `category`. `status in ("triaged", "failed")` — a still-`pending` alert has not finished
    triage and is never a labeling candidate. `AlertRow.id` breaks ties in `received_at DESC` so
    the row order (and hence every downstream `random.Random(seed)` draw) is deterministic for a
    fixed DB state, not merely "usually stable".
    """
    async with session_factory() as session:
        latest = (
            select(VerdictRow.alert_id, func.max(VerdictRow.created_at).label("latest_created_at"))
            .group_by(VerdictRow.alert_id)
            .subquery()
        )
        verdict = aliased(VerdictRow)
        query = (
            select(AlertRow.id, AlertRow.received_at, AlertRow.raw, verdict.category)
            .select_from(AlertRow)
            .outerjoin(latest, latest.c.alert_id == AlertRow.id)
            .outerjoin(
                verdict,
                (verdict.alert_id == latest.c.alert_id)
                & (verdict.created_at == latest.c.latest_created_at),
            )
            .where(AlertRow.status.in_(("triaged", "failed")))
            .order_by(AlertRow.received_at.desc(), AlertRow.id)
        )
        if since is not None:
            query = query.where(AlertRow.received_at >= since)
        rows = (await session.execute(query)).all()
        return [(str(r.id), r.received_at, r.raw, r.category) for r in rows]


def _draw(rng: random.Random, members: Sequence[Candidate], k: int) -> list[Candidate]:
    """`k` members of `members`, seeded by `rng`; every member when `k >= len(members)`."""
    if k >= len(members):
        return list(members)
    return rng.sample(list(members), k)


async def sample(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    n: int,
    seed: int,
    since: datetime | None,
    exclude_case_ids: frozenset[str],
) -> list[Candidate]:
    """Draw a stratified, verdict-blind sample of up to `n` candidates for v2 labeling.

    Every present category stratum (PRD §7.1) gets `max(5, n // 8)` candidates; every
    injection-candidate is included, capped at `n // 4` (PRD §10.6: oversample injection cases);
    the remainder is filled round-robin by `(sensor, day)` from whatever is left over, so a single
    busy sensor/day cannot dominate the sample. `random.Random(seed)` draws within each stratum,
    so the same `(seed, DB state)` always yields the same candidates in the same order.

    Args:
        session_factory: An `async_sessionmaker` bound to the database to read (read-only).
        n: The target sample size.
        seed: The seed for every within-stratum random draw.
        since: When given, only alerts received at or after this time are eligible.
        exclude_case_ids: Case ids (already labeled elsewhere) never returned.

    Returns:
        The sampled `Candidate` list, in deterministic (seed, DB state) order.
    """
    rng = random.Random(seed)
    rows = await _fetch_eligible(session_factory, since=since)

    eligible: list[Candidate] = []
    for alert_id, received_at, raw, category in rows:
        alert = SessionAlert.model_validate(raw)
        case_id = alert.fingerprint()
        if case_id in exclude_case_ids:
            continue
        eligible.append(
            Candidate(
                case_id=case_id,
                alert_id=alert_id,
                received_at=received_at,
                stratum=_stratum_for(alert, category),
                alert=alert,
            )
        )

    buckets: dict[str, list[Candidate]] = {}
    for candidate in eligible:
        buckets.setdefault(candidate.stratum, []).append(candidate)

    selected: dict[str, Candidate] = {}

    for category_name in STRATA_CATEGORIES:
        members = buckets.get(category_name)
        if not members:
            continue
        target = max(5, n // 8)
        for candidate in _draw(rng, members, min(target, len(members))):
            selected[candidate.case_id] = candidate

    injection_members = buckets.get("injection-candidate", [])
    if injection_members:
        cap = n // 4
        for candidate in _draw(rng, injection_members, min(cap, len(injection_members))):
            selected[candidate.case_id] = candidate

    # M1: the per-category floors and the injection cap are each independent minimums and can
    # together already exceed n (e.g. 7 populated categories x floor 5 = 35 > n = 20); clamp with
    # the same seeded rng before the remainder step so `len(result) <= n` always holds.
    if len(selected) > n:
        trimmed = _draw(rng, list(selected.values()), n)
        selected = {c.case_id: c for c in trimmed}

    remaining_budget = max(0, n - len(selected))
    remainder_pool = [c for c in eligible if c.case_id not in selected]
    if remaining_budget and remainder_pool:
        groups: dict[tuple[str, str], list[Candidate]] = {}
        for candidate in remainder_pool:
            key = (candidate.alert.sensor, candidate.received_at.date().isoformat())
            groups.setdefault(key, []).append(candidate)
        group_keys = sorted(groups)
        for key in group_keys:
            rng.shuffle(groups[key])
        index = 0
        while remaining_budget > 0 and any(groups[key] for key in group_keys):
            key = group_keys[index % len(group_keys)]
            if groups[key]:
                candidate = groups[key].pop()
                selected[candidate.case_id] = candidate
                remaining_budget -= 1
            index += 1

    return list(selected.values())


def write_candidates(path: Path, candidates: Sequence[Candidate], *, seed: int = 0) -> int:
    """Write `candidates` as one JSON object per line: no verdict field, ever (PRD §13).

    Ruling R10: `sampled.stratum_id` (not the plain `stratum`) is the only trace of a candidate's
    sampling stratum this file ever carries — the plain stratum name IS the cheap model's
    category by value, so writing it verbatim would anchor the labeler exactly like a verdict
    field would.

    `seed` KEEPS its default (fix-2 Part B asked for "no default", ruling R16; NOT fully applied
    here — see the fix-2 implementer report's "Concerns"): the test-author's fix-2 approved edit
    added `seed=20260914` to `tests/test_label_tool.py`'s three calls, but a FOURTH, still-pinned
    call survives unamended at `tests/test_sample.py:121`
    (`write_candidates(out_path, candidates)`, no `seed=`, file hash unchanged since the original
    `6578c29` RED commit) — outside this round's approved test-author edit scope. Removing the
    default would make that pinned call a `TypeError` with no fix available on the implementer
    side; the default therefore stays until a controller ruling authorizes either a pinned edit to
    `tests/test_sample.py` or a scope change here.

    Args:
        path: The candidate JSONL file to write (overwritten).
        candidates: The candidates to write, in order.
        seed: The sampling run's seed, recorded per row and folded into `stratum_id`.

    Returns:
        The number of lines written.
    """
    lines = []
    for candidate in candidates:
        row = {
            "case_id": candidate.case_id,
            "alert": candidate.alert.model_dump(mode="json"),
            "sampled": {
                "alert_id": candidate.alert_id,
                "received_at": candidate.received_at.isoformat(),
                "stratum_id": stratum_id(candidate.stratum, seed),
                "seed": seed,
            },
        }
        lines.append(json.dumps(row))
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return len(candidates)


def _parse_since(value: str) -> datetime:
    """Parse `--since`'s ISO date/datetime string; a bare date is treated as UTC midnight."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _load_exclude_case_ids(path: Path) -> frozenset[str]:
    """Case ids to exclude, read from a candidate file or a golden file (either row shape).

    A candidate row carries `case_id` directly; a golden row does not (it is a computed
    property), so its case id is recomputed from `alert.fingerprint()` — this module never
    imports the golden-set label type (PRD §13).
    """
    ids: set[str] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "case_id" in row:
            ids.add(row["case_id"])
        else:
            ids.add(SessionAlert.model_validate(row["alert"]).fingerprint())
    return frozenset(ids)


def main(argv: Sequence[str] | None = None) -> int:
    """Sample v2 candidates from the live database and write them to a candidate JSONL file.

    Builds its own engine/session factory exactly as `scripts/check_real_sessions.py` does, and
    disposes the engine before returning.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.

    Returns:
        `0` on success (the candidate file was written); `1` via `core.cli.fail(e.code, ...)` on
        a malformed command line, `fail("config_error", ...)` on a missing database URL or a
        `Settings()` validation failure, or `fail("database_error", ...)` (exception class name
        only) on a DB failure — never prints a URL or a raw exception message/traceback.
    """
    parser = Parser(prog="python -m evals.sample")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--since", default=None)
    parser.add_argument("--exclude", type=Path, default=None)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    database_url = args.database_url
    if database_url is None:
        # Ruling R14/M8: the DB URL seam is `--database-url` or `Settings().database_url` only —
        # never a direct `os.environ` read (the prior `DATABASE_URL`-or-`TEST_DATABASE_URL`
        # fallback could silently pick up an unrelated test database on the author's machine).
        try:
            settings = Settings()
        except ValidationError as e:
            return fail("config_error", str(e))
        database_url = settings.database_url.get_secret_value()
    if not database_url:
        return fail("config_error", "no database URL (pass --database-url or set DATABASE_URL)")

    since = _parse_since(args.since) if args.since else None
    exclude_case_ids = _load_exclude_case_ids(args.exclude) if args.exclude else frozenset()

    async def _run() -> list[Candidate]:
        engine = make_engine(database_url, schema=args.schema)
        try:
            factory = make_session_factory(engine)
            return await sample(
                factory, n=args.n, seed=args.seed, since=since, exclude_case_ids=exclude_case_ids
            )
        finally:
            await engine.dispose()

    try:
        candidates = asyncio.run(_run())
    except (OSError, SQLAlchemyError) as e:
        return fail("database_error", f"{type(e).__name__}: could not sample alerts")

    write_candidates(args.out, candidates, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
