"""Dev-only seed script: fills a local database with 25 triaged alerts (m3 task-06).

`uv run python scripts/seed_dev.py` runs the five `fixtures/alerts/*.json` sessions and the
twenty `evals/golden/v1.jsonl` sessions through the **production** write path
(`insert_alert` -> `TriagePipeline.triage_alert` -> `persist_verdict`), so seeded rows are shaped
exactly like ingested ones. By default each alert is triaged by a fresh
`tests.fakes.FakeLLMClient` replaying a canned verdict built (through `Verdict`, so it always
validates) from the fixture's intended band or the golden case's label; `--live` opts into the
real `OpenAICompatibleLLMClient` and refuses to run without `LLM_API_KEY`. A second run creates 0
new rows (PRD §6.1 dedup).

m4 task-06: every alert also runs through the five-tool registry (`worker.tools.wiring
.build_registry`) so seeded rows carry real tool-call traces for task-07's timeline. Each of the
five `fixtures/alerts/*.json` sessions scripts one tool turn from `FIXTURE_TOOL_TURNS`, replayed
against `tests/fixtures/tools/` (`select_recorder`); every golden-set row scripts none (the model
just answers directly). m5 task-05: `load_candidates` itself now returns `(alert, canned, tool
names)` triples — computing each candidate's tool names from its own single glob of `fixtures` —
so `main()` no longer needs a second, separately-globbed pass to line a per-candidate tool-name
list up with `load_candidates`'s own fixture order; it passes `load_candidates`'s triples straight
to `seed()`.

Runs from a repo checkout on the host — it imports `tests.fakes` (lazily, only on the fake path)
— never inside the api image and never from a compose `command:` (PRD §10.1; M2 final review,
plan defect 9).

No `from __future__ import annotations` here (unlike most of this codebase): the pinned
`tests/test_seed_dev.py` loads this file via `importlib.util.spec_from_file_location` without
registering it in `sys.modules`, and `dataclasses` (`SeedCounts` below) needs to resolve
postponed (string) annotations through `sys.modules[cls.__module__]`, which is `None` under that
loading path — a stdlib `AttributeError` at import time. Python 3.12 evaluates `X | Y` and
`dict[str, X]` natively, so nothing here actually depends on postponed evaluation.
"""

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from core.cli import Parser, UsageError, fail
from core.config import Settings
from core.db import make_engine, make_session_factory
from core.errors import ConfigError
from core.llm import LLMClient
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict, VerdictCategory
from core.services.alerts import insert_alert
from evals.golden import load_golden
from worker.llm_client import OpenAICompatibleLLMClient
from worker.tools import LiveToolRecorder, ReplayToolRecorder, ToolRecorder
from worker.tools.wiring import build_registry
from worker.triage import TriagePipeline

if TYPE_CHECKING:
    from tests.fakes import ScriptedToolCall

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = REPO_ROOT / "evals" / "golden" / "v1.jsonl"
DEFAULT_FIXTURES = REPO_ROOT / "fixtures" / "alerts"
DEFAULT_TOOL_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "tools"
FAKE_MODEL = (
    "seed-fake"  # model_primary/model_final on fake-seeded verdicts, so they are recognizable
)

FIXTURE_TOOL_TURNS: dict[str, tuple[str, ...]] = {  # one tool turn per fixture alert (m4 task-06)
    "alert1": ("get_ip_geo_asn",),
    "alert2": ("get_ip_geo_asn",),
    "alert3": ("get_ip_geo_asn",),
    "alert4": ("get_session_commands", "get_ip_geo_asn"),
    "alert5": ("get_session_commands", "get_ip_geo_asn"),
}

FIXTURE_LABELS: dict[
    str, tuple[int, VerdictCategory, bool]
] = {  # per fixtures/alerts/README.md bands
    "alert1": (1, "scanning", False),
    "alert2": (2, "brute_force", False),
    "alert3": (3, "brute_force", False),
    "alert4": (4, "successful_intrusion", True),
    "alert5": (5, "malware_delivery", True),
}
DEFAULT_FIXTURE_LABEL: tuple[int, VerdictCategory, bool] = (
    2,
    "other",
    False,
)  # any other *.json in --fixtures
CONFIDENCE_BY_SEVERITY: dict[int, float] = {1: 0.85, 2: 0.80, 3: 0.75, 4: 0.90, 5: 0.95}
PHRASE_BY_CATEGORY: dict[VerdictCategory, str] = {
    "scanning": "probed the SSH service and disconnected",
    "brute_force": "attempted repeated credential logins",
    "successful_intrusion": "logged in and ran commands",
    "malware_delivery": "logged in and fetched a remote payload",
    "persistence_attempt": "logged in and installed a persistence mechanism",
    "reconnaissance": "enumerated the host",
    "other": "produced an unclassified session",
}
ACTION_BY_CATEGORY: dict[VerdictCategory, str] = {
    "scanning": "No action; keep monitoring the source range.",
    "brute_force": "Block the source IP at the edge and confirm no login succeeded.",
    "successful_intrusion": (
        "Isolate the host, rotate the exposed credentials, and review the command history."
    ),
    "malware_delivery": (
        "Isolate the host, capture the downloaded payload hash, and block the download URL."
    ),
    "persistence_attempt": (
        "Isolate the host and remove the persistence mechanism (cron, keys, services)."
    ),
    "reconnaissance": "Review what was enumerated and watch the source IP for follow-up activity.",
    "other": "Review the session manually.",
}


def canned_verdict(
    alert: SessionAlert, *, severity: int, category: VerdictCategory, escalate: bool
) -> str:
    """Build a canned `Verdict` reply for `alert`, citing its source IP and first username.

    Built through `Verdict` itself (never a hand-rolled JSON string), so the reply always
    validates, including the PRD §6.6 escalate rule.

    Args:
        alert: The session alert the canned reply is for.
        severity: The verdict's severity (1-5).
        category: The verdict's category.
        escalate: The verdict's escalate flag.

    Returns:
        The `Verdict`'s JSON serialization, as `FakeLLMClient` expects to replay it.
    """
    username = next((e.username for e in alert.events if e.username is not None), "(none)")
    reasoning = (
        f"{alert.src_ip} {PHRASE_BY_CATEGORY[category]} against sensor {alert.sensor} "
        f"in session {alert.session_id}. "
        f"Username {username!r} was observed; {len(alert.events)} events were recorded."
    )
    verdict = Verdict(
        severity=severity,
        category=category,
        confidence=CONFIDENCE_BY_SEVERITY[severity],
        reasoning=reasoning,
        recommended_action=ACTION_BY_CATEGORY[category],
        escalate=escalate,
    )
    return verdict.model_dump_json()


def scripted_tool_turn(alert: SessionAlert, names: Sequence[str]) -> "list[ScriptedToolCall]":
    """Build one scripted tool turn for `alert` naming `names` (m4 task-06, `FIXTURE_TOOL_TURNS`).

    Imports `tests.fakes` lazily, like the `FakeLLMClient` branch in `seed()` — this module must
    still import without `tests/` present.

    Args:
        alert: The fixture alert the turn is scripted for.
        names: The tool names to script, in call order (e.g. `FIXTURE_TOOL_TURNS["alert4"]`).

    Returns:
        One `ScriptedToolCall` per name in `names`, with the arguments each tool needs from
        `alert`.
    """
    from tests.fakes import ScriptedToolCall

    arguments_by_name: dict[str, dict[str, object]] = {
        "get_ip_geo_asn": {"ip": alert.src_ip},
        "get_session_commands": {"session_id": alert.session_id},
    }
    return [ScriptedToolCall(name, arguments_by_name[name]) for name in names]


def select_recorder(*, live: bool) -> ToolRecorder:
    """Pick the tool recorder for a seed run (m4 task-06): live execution, or fixture replay.

    Args:
        live: `True` selects `LiveToolRecorder` (matches `--live`'s real LLM client); `False`
            selects `ReplayToolRecorder` over `DEFAULT_TOOL_FIXTURES_DIR`, so external tools never
            hit the network even when triage itself runs live.

    Returns:
        A `ToolRecorder` for `build_registry`.
    """
    if live:
        return LiveToolRecorder()
    return ReplayToolRecorder(DEFAULT_TOOL_FIXTURES_DIR)


def load_candidates(
    *, golden: Path, fixtures: Path
) -> list[tuple[SessionAlert, str, tuple[str, ...]]]:
    """Load every fixture and golden-set case as `(alert, canned verdict json, tool names)`.

    Fixtures come first, sorted by filename, followed by the golden-set cases in file order. Each
    fixture row carries its own `FIXTURE_TOOL_TURNS` entry (m4 task-06); every golden-set row
    carries `()` — the model just answers directly (m5 task-05: computed here, once, so `main()`
    no longer re-globs `fixtures` a second time to line a tool-name list up with this order).

    Args:
        golden: Path to the golden-set JSONL file.
        fixtures: Path to the directory of fixture `*.json` files.

    Returns:
        25 `(SessionAlert, canned verdict json, tool names)` triples: 5 fixtures then 20 golden
        cases.

    Raises:
        ValueError: A golden row is invalid, or a fixture fails `SessionAlert` validation.
        OSError: `fixtures` is not a directory, or `golden`/a fixture file is unreadable.
    """
    if not fixtures.is_dir():
        raise OSError(f"{fixtures} is not a directory")

    candidates: list[tuple[SessionAlert, str, tuple[str, ...]]] = []

    for path in sorted(fixtures.glob("*.json")):
        alert = SessionAlert.model_validate_json(path.read_text())
        severity, category, escalate = FIXTURE_LABELS.get(path.stem, DEFAULT_FIXTURE_LABEL)
        candidates.append(
            (
                alert,
                canned_verdict(alert, severity=severity, category=category, escalate=escalate),
                FIXTURE_TOOL_TURNS.get(path.stem, ()),
            )
        )

    for case in load_golden(golden):
        candidates.append(
            (
                case.alert,
                canned_verdict(
                    case.alert,
                    severity=case.label.severity,
                    category=case.label.category,
                    escalate=case.label.escalate,
                ),
                (),
            )
        )

    return candidates


@dataclass(frozen=True)
class SeedCounts:
    """The outcome of one `seed()` run: how many alerts were created, skipped, and failed triage."""

    created: int
    skipped: int
    failed: int


async def seed(
    candidates: Sequence[tuple[SessionAlert, str, Sequence[str]]],
    *,
    database_url: str,
    schema: str | None,
    llm: LLMClient | None,
    model: str,
    prompt_version: str,
    recorder: ToolRecorder,
    settings: Settings,
    strong_model: str | None = None,
) -> SeedCounts:
    """Insert and triage every candidate through the production write path.

    Duplicates (an existing fingerprint) are skipped without triggering triage — PRD §6.1: a
    re-run must never double-trigger an LLM call. A fresh `FakeLLMClient` is built per alert
    (when `llm` is not injected) so that a skip can never desync the fake's response queue; when
    the candidate names a tool turn (`FIXTURE_TOOL_TURNS`, m4 task-06), that fake's script is
    `[scripted_tool_turn(alert, names), canned]` so the run's one tool turn plus its final verdict
    both come from the same fake, in order.

    Args:
        candidates: `(SessionAlert, canned verdict json, tool names)` triples — `main()` builds
            these by zipping `load_candidates`'s pairs with each candidate's `FIXTURE_TOOL_TURNS`
            entry (`()` for every golden-set row).
        database_url: The Postgres URL to seed.
        schema: Optional schema to pin the connection's search_path to.
        llm: An `LLMClient` to use for every alert instead of a fresh `FakeLLMClient` per alert
            — the seam tests inject `FakeLLMClient` through. Bypasses per-alert tool scripting:
            the same injected client answers every alert, exactly as before m4 task-06.
        model: The model id recorded on every seeded verdict.
        prompt_version: The prompt version every alert is triaged with.
        recorder: How the five enrichment tools are executed (`select_recorder`).
        settings: The config surface `build_registry` wires every tool's bounds from.
        strong_model: Two-tier routing's strong id (PRD §6.4, m5 task-03); `None` (default) keeps
            routing off. `main()` only ever passes a value on `--live` — a canned
            `FakeLLMClient` has no strong-tier reply scripted, so the fake path always leaves this
            `None`.

    Returns:
        Counts of created, skipped, and failed-triage alerts.
    """
    # Built once, not per alert (m4 task-06 fix-1, I3): the registry is stateless w.r.t. the
    # alert being triaged — the alert travels in `ToolContext`, not in any tool's constructor —
    # so a fresh `httpx.AsyncClient`, YAML parse and pair of `.mmdb` file handles per alert would
    # be 25 unclosed resources for nothing.
    registry = build_registry(settings, recorder=recorder)

    engine = make_engine(database_url, schema=schema)
    factory = make_session_factory(engine)
    created = 0
    skipped = 0
    failed = 0
    try:
        async with factory() as session:
            for alert, canned, names in candidates:
                result = await insert_alert(session, alert)
                await session.commit()  # same commit-before-triage as the ingest route
                if not result.created:
                    skipped += 1
                    continue

                if llm is not None:
                    client = llm
                else:
                    from tests.fakes import FakeLLMClient

                    if names:
                        client = FakeLLMClient([scripted_tool_turn(alert, names), canned])
                    else:
                        client = FakeLLMClient([canned])

                pipeline = TriagePipeline(
                    llm=client,
                    model=model,
                    prompt_version=prompt_version,
                    tools=registry,
                    tool_loop_max_iter=settings.tool_loop_max_iter,
                    strong_model=strong_model,
                    escalate_severity_gte=settings.escalate_severity_gte,
                    escalate_confidence_lt=settings.escalate_confidence_lt,
                )
                status = await pipeline.triage_alert(session, result.alert_id)
                created += 1
                failed += status == "failed"
    finally:
        await engine.dispose()

    return SeedCounts(created=created, skipped=skipped, failed=failed)


def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int:
    """Seed a database with 25 triaged alerts through the production write path.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        llm: An `LLMClient` to use for every alert instead of a fresh `FakeLLMClient` per alert
            — the seam tests inject `FakeLLMClient` through (CONVENTIONS.md §10). Beats `--live`.

    Returns:
        `0` on success (`created=<n> skipped=<n> failed=<n>` printed to stdout — failed triages
        are reported, never fatal); `1` on a usage error, a `Settings()` validation failure, a
        missing database URL, `--live` without `LLM_API_KEY`, `--live` with `STRONG_MODEL` equal
        to `CHEAP_MODEL`, a `--live` client construction `ConfigError`, an invalid/missing golden
        file, an invalid/missing fixtures directory, or a database error.
    """
    parser = Parser(prog="seed_dev.py")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    try:
        settings = Settings()
    except ValidationError as e:
        return fail("config_error", str(e))

    database_url = args.database_url
    if database_url is None:
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL") or ""
    if not database_url:
        return fail("config_error", "no database URL (pass --database-url or set DATABASE_URL)")

    if args.live and not settings.llm_api_key.get_secret_value():
        return fail("config_error", "--live requires LLM_API_KEY")

    # llm= (tests) beats --live for which client seed() uses; --live alone still selects the
    # cheap_model routing tier (only --live prices verdicts against a real model id).
    client: LLMClient | None = llm
    model = settings.cheap_model if args.live else FAKE_MODEL
    # Two-tier routing (m5 task-03): only `--live` opts in, since a canned `FakeLLMClient` never
    # has a strong-tier reply scripted (PRD §6.4).
    strong_model = (settings.strong_model or None) if args.live else None
    # Checked up front (m5 task-03 fix-1, review I1), before any insert: `TriagePipeline` itself
    # raises a bare `ValueError` for this, which would otherwise escape `seed()` as a traceback.
    if args.live and strong_model == model:
        return fail("config_error", "STRONG_MODEL must differ from CHEAP_MODEL")
    if client is None and args.live:
        try:
            client = OpenAICompatibleLLMClient.from_settings(settings)
        except ConfigError as e:
            return fail(e.code, str(e))

    # Golden checked before fixtures (check-order items 6, 7): each is validated on its own,
    # before `load_candidates` re-walks both to build the actual (fixtures-first) candidate list,
    # so a failure is unambiguously attributable to the file that caused it.
    try:
        load_golden(args.golden)
    except (ValueError, OSError) as e:
        return fail("invalid_golden", str(e))

    if not args.fixtures.is_dir():
        return fail("invalid_fixtures", f"{args.fixtures} is not a directory")
    try:
        for path in sorted(args.fixtures.glob("*.json")):
            SessionAlert.model_validate_json(path.read_text())
    except (ValueError, OSError) as e:
        return fail("invalid_fixtures", str(e))

    # m5 task-05: `load_candidates` itself computes each candidate's tool names now, from its own
    # single glob of `args.fixtures` — no second, separately-globbed pass to line them up.
    triples = load_candidates(golden=args.golden, fixtures=args.fixtures)

    recorder = select_recorder(live=args.live)
    try:
        counts = asyncio.run(
            seed(
                triples,
                database_url=database_url,
                schema=args.schema,
                llm=client,
                model=model,
                prompt_version=settings.triage_prompt_version,
                recorder=recorder,
                settings=settings,
                strong_model=strong_model,
            )
        )
    except (OSError, SQLAlchemyError) as e:
        return fail("database_error", str(e))

    print(f"created={counts.created} skipped={counts.skipped} failed={counts.failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
