"""`evals.record`: mint the tool fixtures a golden set's cases can request, once (PRD §7.2, m7
task-02).

`python -m evals.record --golden evals/golden/v2.jsonl [--fixtures tests/fixtures/tools]
[--only get_ip_geo_asn] [--dry-run]` walks every case in `--golden` and calls `get_ip_geo_asn` and
`lookup_ip_reputation` — the only two external tools this module can enumerate every argument set
for, each keyed on the session's own `src_ip` only (`{"ip": ...}`) — live, exactly ONCE per
distinct `(tool, ip)` pair, writing fixtures with the existing `worker.tools.write_fixture` layout
under `--fixtures` (`tests/fixtures/tools/<tool>/<key>.json`, m4 task-01) so a v2 case's fixture
can be minted once and replayed deterministically forever after (`.claude/rules/evals.md`).
`get_alert_history` is also `external = True` and IS replayed from a fixture at eval time
(`worker/tools/alert_history.py`), but its `window_hours` argument is the model's free choice —
this module cannot enumerate every value the model might ask for, so it is never recorded here and
always replays leniently in `evals.run` regardless of `--replay-strict` (ruling R26, m7 task-02
fix-1; `worker.tools.STRICT_TOOL_NAMES`).

Idempotent: an existing fixture is never re-recorded. Persistence is fail-closed (ruling R30,
re-review N2): only an `unavailable(reason)` result whose `reason` is in
`worker.tools.DETERMINISTIC_REASONS` — the tokens a tool's own argument/lookup logic can produce
(`invalid_arguments`, `unknown_session`, `unknown_asset`), which reproduce identically forever —
is ever persisted. Every OTHER reason is transient: a fixed environment-failure token
(`no_api_key`, `quota_exceeded`, ...), a DYNAMICALLY formatted one (`IpReputationTool`'s
`f"http_{status}"` for an HTTP error it doesn't special-case — no fixed deny-list could ever
enumerate this), or anything nobody has named yet. A transient result's fixture — the file
`LiveToolRecorder` already wrote, since it does not know the recording is transient — is removed
and the call is reported under `failed` instead, so a partial Step-6 run against an unconfigured
environment, or one that hits a vendor 5xx partway through, can never silently poison a committed
fixture. `--dry-run` prints the plan and calls nothing. `--only <tool>` restricts recording to one
tool name.

This module is the OWNER's tool, run once against the real APIs (Step 6, m7 task-02's brief) —
nothing here is exercised against a live network in CI; `tests/test_record.py`/
`tests/test_record_poison.py` drive `record()` and `main()` entirely through in-file
external-tool stubs (CONVENTIONS.md §10), never a real `Settings()`/API-key path unless the caller
omits the `registry=` injection seam. Recording is deliberately label-agnostic: it reads only
`case.alert.src_ip`, never `labeled_by`, so fixtures can be minted for a not-yet-human-labeled
`v2-candidates.jsonl` row before the labeling pass — unlike `evals.run`, which enforces PRD §13's
human-label requirement before scoring a v2 file (m7 task-02 fix-1, review M4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from core.cli import Parser, UsageError, fail
from core.config import Settings
from core.schemas.alert import CowrieEvent, SessionAlert
from evals.golden import GoldenCase, load_golden
from worker.tools import (
    DETERMINISTIC_REASONS,
    LiveToolRecorder,
    ToolContext,
    ToolRegistry,
    fixture_key,
    fixture_path,
)
from worker.tools.wiring import build_registry

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tools"

# The pipeline's only two EXTERNAL tools whose complete argument set this module can enumerate
# from a `GoldenCase` alone (PRD §6.3); `get_alert_history`'s `window_hours` is the model's free
# choice and is never planned here (ruling R26, review I1).
EXTERNAL_TOOL_NAMES: tuple[str, ...] = ("get_ip_geo_asn", "lookup_ip_reputation")


@dataclass(frozen=True)
class RecordReport:
    """The outcome of one `record()` invocation."""

    planned: int  # len(the possibly `only`-filtered plan) — set even under dry_run
    recorded: int  # newly written fixtures
    skipped_existing: int  # planned calls whose fixture already existed
    failed: list[tuple[str, str, str]]  # (tool_name, fixture_key, reason/exception class name)


def planned_calls(cases: Sequence[GoldenCase]) -> list[tuple[str, dict[str, Any]]]:
    """The exact `(tool, arguments)` set the pipeline can request for `cases` (PRD §6.3).

    Every tool in `EXTERNAL_TOOL_NAMES` takes only the session's own `src_ip` as its argument, so
    the planned call set is exactly one `{"ip": ...}` call per tool per distinct `src_ip` across
    `cases`.

    Args:
        cases: The golden-set cases to plan calls for.

    Returns:
        `(tool_name, {"ip": src_ip})` tuples, de-duplicated and sorted first by tool name (in
        `EXTERNAL_TOOL_NAMES` order) then by `src_ip`.
    """
    ips = sorted({case.alert.src_ip for case in cases})
    return [(tool_name, {"ip": ip}) for tool_name in EXTERNAL_TOOL_NAMES for ip in ips]


def _filtered_calls(
    cases: Sequence[GoldenCase], only: str | None
) -> list[tuple[str, dict[str, Any]]]:
    """`planned_calls(cases)`, restricted to `only`'s tool name when given."""
    calls = planned_calls(cases)
    if only is not None:
        calls = [(tool_name, arguments) for tool_name, arguments in calls if tool_name == only]
    return calls


def _record_ctx() -> ToolContext:
    """The one placeholder `ToolContext` every recording call runs under.

    Both tools this module records (`worker/tools/geo_asn.py`, `worker/tools/ip_reputation.py`)
    answer from `arguments["ip"]` alone and never read `ctx.alert` — so one minimal, always-valid
    session, reused for every planned call, is enough; `now` is real wall-clock time (recording is
    not a determinism-sensitive path the way replay is).
    """
    now = datetime.now(UTC)
    alert = SessionAlert(
        source="cowrie",
        session_id="evals-record",
        src_ip="0.0.0.0",
        sensor="evals-record",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=now,
                session="evals-record",
                src_ip="0.0.0.0",
                sensor="evals-record",
            )
        ],
    )
    return ToolContext(alert=alert, session=None, now=now)


async def record(
    cases: Sequence[GoldenCase],
    *,
    registry: ToolRegistry,
    fixtures_dir: Path,
    only: str | None,
    dry_run: bool,
) -> RecordReport:
    """Mint every fixture `planned_calls(cases)` names, skipping any that already exist.

    Calls each planned `(tool_name, arguments)` pair through `registry.execute` — never a bare
    `tool.run` — so a raising tool is reported the same shape a real misbehaving tool would be
    (`ToolRegistry.execute`'s own `unavailable("<ExceptionClass>: tool raised")` backstop,
    controller ruling Q6). Two kinds of failure share the `failed` list, distinguished only by
    their reason token's shape (never by a separate discriminator field, so the tuple stays the
    stable 3-tuple every caller already destructures): a raising tool's exception CLASS name
    (`"RuntimeError"`, parsed from the registry's `"<ExceptionClass>: tool raised"` shape — the
    raised message itself never reaches the report or a fixture file, PRD §10.6), or a transient
    `unavailable(reason)` result's own `reason` token verbatim (`"no_api_key"`, `"http_503"`,
    ... — anything NOT in `worker.tools.DETERMINISTIC_REASONS`, ruling R30's fail-closed
    ALLOW-list). Neither ever leaves a fixture behind: a raising tool never wrote one
    (`LiveToolRecorder.execute` calls `write_fixture` only after `tool.run` returns), and a
    transient `unavailable` result's file — which `LiveToolRecorder` writes unconditionally, since
    it does not know the recording is transient — is unlinked before being reported (ruling R25,
    tightened to fail-closed by R30, review C1/N2). A DETERMINISTIC `unavailable` result (`reason`
    IN `DETERMINISTIC_REASONS`: `invalid_arguments`, `unknown_session`, `unknown_asset`) is the
    opposite: it is persisted like a real answer, because the tool's own logic — not the owner's
    environment, and not an open-ended vendor status code — produced it and it reproduces
    identically on every future run.

    Args:
        cases: The golden-set cases to record fixtures for.
        registry: The tool registry to execute calls through; the caller wires it with a
            `LiveToolRecorder(record_dir=fixtures_dir)` so a successful call also writes the
            fixture (`worker.tools.write_fixture` layout, m4 task-01).
        fixtures_dir: Where fixtures are read from (to check "already exists") and removed from
            (a poisoned transient result).
        only: Restrict recording to this tool name; `None` records every planned call.
        dry_run: When `True`, plan only — `registry` is never touched and nothing is written.

    Returns:
        `RecordReport(planned=len(the possibly `only`-filtered plan), recorded=<newly written
        count>, skipped_existing=<already-present count>, failed=<[(tool, key, reason), ...]>)`.
        Under `dry_run`, `recorded`/`skipped_existing`/`failed` are all zero/empty.
    """
    calls = _filtered_calls(cases, only)
    if dry_run:
        return RecordReport(planned=len(calls), recorded=0, skipped_existing=0, failed=[])

    ctx = _record_ctx()
    recorded = 0
    skipped_existing = 0
    failed: list[tuple[str, str, str]] = []
    for tool_name, arguments in calls:
        path = fixture_path(fixtures_dir, tool_name, arguments)
        if path.exists():
            skipped_existing += 1
            continue
        execution = await registry.execute(tool_name, arguments, ctx)
        if execution.result.get("unavailable"):
            reason = str(execution.result.get("reason", ""))
            if reason not in DETERMINISTIC_REASONS:
                # Fail-closed (ruling R30): only a tool's own argument/lookup logic — a reason IN
                # DETERMINISTIC_REASONS — reproduces identically forever. Everything else never
                # wrote a file (a raising tool's own backstop reason, "<ExceptionClass>: tool
                # raised", detected by its colon) or did (a transient `unavailable` result —
                # fixed or dynamic, e.g. `IpReputationTool`'s `f"http_{status}"` — since
                # LiveToolRecorder writes unconditionally) — either way this call never reproduces
                # reliably, so remove whatever is there and report it as a failure, never a
                # fixture.
                path.unlink(missing_ok=True)
                reported_reason = reason.split(":", 1)[0] if ":" in reason else reason
                failed.append((tool_name, fixture_key(arguments), reported_reason))
                continue
            # A deterministic `unavailable` result: falls through to the M2 check below exactly
            # like a real answer.

        if not path.exists():
            # M2 (review): the injected registry's recorder never actually wrote a fixture (e.g.
            # a bare LiveToolRecorder() with no record_dir) — a mis-wired registry must be a loud
            # failure, never a silent "recorded" count over an empty fixtures directory.
            failed.append((tool_name, fixture_key(arguments), "not_recorded"))
            continue
        recorded += 1

    return RecordReport(
        planned=len(calls), recorded=recorded, skipped_existing=skipped_existing, failed=failed
    )


async def _record_all(
    cases: Sequence[GoldenCase],
    *,
    registry: ToolRegistry,
    fixtures_dir: Path,
    only: str | None,
    http: httpx.AsyncClient | None,
) -> RecordReport:
    """The one coroutine `main`'s non-dry-run path runs: `record()`, then closing the
    process-lifetime `http` client this call built (`None` when a `registry` was injected — the
    seam test owns whatever client it wired) — mirrors `evals.run._run_all`'s "one coroutine, one
    asyncio.run" shape so `http` never crosses two separately-created event loops.
    """
    try:
        return await record(
            cases, registry=registry, fixtures_dir=fixtures_dir, only=only, dry_run=False
        )
    finally:
        if http is not None:
            await http.aclose()


def main(argv: Sequence[str] | None = None, *, registry: ToolRegistry | None = None) -> int:
    """Record every fixture `--golden`'s cases can request, once.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        registry: A `ToolRegistry` to record through instead of building the real one from
            `Settings()` — the seam tests inject a canned registry through (CONVENTIONS.md §10).
            `None` builds the real five-tool registry wired with `LiveToolRecorder(record_dir=
            <--fixtures>)` over a fresh `httpx.AsyncClient`, closed before returning.

    Returns:
        `0` on success, including `--dry-run` and a run with no failures; `1` on a usage error,
        an invalid/missing golden file, a `Settings()` validation failure, or when `record()`
        reports any `failed` entry — printed to stderr as tool/key/reason names only, never a
        raised message (PRD §10.6).
    """
    parser = Parser(prog="python -m evals.record")
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--only", default=None)
    parser.add_argument("--dry-run", action="store_true")
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    try:
        cases = load_golden(args.golden)
    except (OSError, ValueError) as e:
        return fail("invalid_golden", str(e))

    if args.dry_run:
        for tool_name, arguments in _filtered_calls(cases, args.only):
            print(f"{tool_name} {arguments['ip']}")
        return 0

    http_client: httpx.AsyncClient | None = None
    if registry is not None:
        used_registry = registry
    else:
        try:
            settings = Settings()
        except ValidationError as e:
            return fail("config_error", str(e))
        http_client = httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s)
        used_registry = build_registry(
            settings, recorder=LiveToolRecorder(record_dir=args.fixtures), http=http_client
        )

    report = asyncio.run(
        _record_all(
            cases,
            registry=used_registry,
            fixtures_dir=args.fixtures,
            only=args.only,
            http=http_client,
        )
    )

    if report.failed:
        for tool_name, key, reason_class in report.failed:
            fail("record_failed", f"{tool_name} {key} {reason_class}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
