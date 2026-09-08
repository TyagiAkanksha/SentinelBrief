"""Dev-only seed script: fills a local database with 25 triaged alerts (m3 task-06).

`uv run python scripts/seed_dev.py` runs the five `fixtures/alerts/*.json` sessions and the
twenty `evals/golden/v1.jsonl` sessions through the **production** write path
(`insert_alert` -> `TriagePipeline.triage_alert` -> `persist_verdict`), so seeded rows are shaped
exactly like ingested ones. By default each alert is triaged by a fresh
`tests.fakes.FakeLLMClient` replaying a canned verdict built (through `Verdict`, so it always
validates) from the fixture's intended band or the golden case's label; `--live` opts into the
real `OpenAICompatibleLLMClient` and refuses to run without `LLM_API_KEY`. A second run creates 0
new rows (PRD §6.1 dedup).

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

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from core.config import Settings
from core.db import make_engine, make_session_factory
from core.errors import ConfigError, SentinelBriefError
from core.llm import LLMClient
from core.schemas.alert import SessionAlert
from core.schemas.verdict import Verdict, VerdictCategory
from core.services.alerts import insert_alert
from evals.golden import load_golden
from worker.llm_client import OpenAICompatibleLLMClient
from worker.triage import TriagePipeline

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = REPO_ROOT / "evals" / "golden" / "v1.jsonl"
DEFAULT_FIXTURES = REPO_ROOT / "fixtures" / "alerts"
FAKE_MODEL = (
    "seed-fake"  # model_primary/model_final on fake-seeded verdicts, so they are recognizable
)

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


def load_candidates(*, golden: Path, fixtures: Path) -> list[tuple[SessionAlert, str]]:
    """Load every fixture and golden-set case as `(SessionAlert, canned verdict json)` pairs.

    Fixtures come first, sorted by filename, followed by the golden-set cases in file order.

    Args:
        golden: Path to the golden-set JSONL file.
        fixtures: Path to the directory of fixture `*.json` files.

    Returns:
        25 `(SessionAlert, canned verdict json)` pairs: 5 fixtures then 20 golden cases.

    Raises:
        ValueError: A golden row is invalid, or a fixture fails `SessionAlert` validation.
        OSError: `fixtures` is not a directory, or `golden`/a fixture file is unreadable.
    """
    if not fixtures.is_dir():
        raise OSError(f"{fixtures} is not a directory")

    candidates: list[tuple[SessionAlert, str]] = []

    for path in sorted(fixtures.glob("*.json")):
        alert = SessionAlert.model_validate_json(path.read_text())
        severity, category, escalate = FIXTURE_LABELS.get(path.stem, DEFAULT_FIXTURE_LABEL)
        candidates.append(
            (alert, canned_verdict(alert, severity=severity, category=category, escalate=escalate))
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
    candidates: Sequence[tuple[SessionAlert, str]],
    *,
    database_url: str,
    schema: str | None,
    llm: LLMClient | None,
    model: str,
    prompt_version: str,
) -> SeedCounts:
    """Insert and triage every candidate through the production write path.

    Duplicates (an existing fingerprint) are skipped without triggering triage — PRD §6.1: a
    re-run must never double-trigger an LLM call. A fresh `FakeLLMClient` is built per alert
    (when `llm` is not injected) so that a skip can never desync the fake's response queue.

    Args:
        candidates: `(SessionAlert, canned verdict json)` pairs, e.g. from `load_candidates`.
        database_url: The Postgres URL to seed.
        schema: Optional schema to pin the connection's search_path to.
        llm: An `LLMClient` to use for every alert instead of a fresh `FakeLLMClient` per alert
            — the seam tests inject `FakeLLMClient` through.
        model: The model id recorded on every seeded verdict.
        prompt_version: The prompt version every alert is triaged with.

    Returns:
        Counts of created, skipped, and failed-triage alerts.
    """
    engine = make_engine(database_url, schema=schema)
    factory = make_session_factory(engine)
    created = 0
    skipped = 0
    failed = 0
    try:
        async with factory() as session:
            for alert, canned in candidates:
                result = await insert_alert(session, alert)
                await session.commit()  # same commit-before-triage as the ingest route
                if not result.created:
                    skipped += 1
                    continue

                if llm is not None:
                    client = llm
                else:
                    from tests.fakes import FakeLLMClient

                    client = FakeLLMClient([canned])

                pipeline = TriagePipeline(llm=client, model=model, prompt_version=prompt_version)
                status = await pipeline.triage_alert(session, result.alert_id)
                created += 1
                failed += status == "failed"
    finally:
        await engine.dispose()

    return SeedCounts(created=created, skipped=skipped, failed=failed)


def _fail(code: str, message: str) -> int:
    """Print one flattened `error: <code>: <message>` line to stderr; never a traceback.

    Args:
        code: The stable wire code for this failure (e.g. `"config_error"`).
        message: A human-readable description; embedded newlines are collapsed so the line
            stays exactly one line, matching every other CLI's error shape.

    Returns:
        Always `1` — every caller of this helper is a `1`-exit-code path.
    """
    print(f"error: {code}: {' '.join(message.split())}", file=sys.stderr)
    return 1


class UsageError(SentinelBriefError):
    """CLI-only: raised by `_Parser.error` instead of letting argparse exit the process directly."""

    code = "usage"


class _Parser(argparse.ArgumentParser):
    """An `ArgumentParser` that raises `UsageError` on a usage error instead of exiting."""

    def error(self, message: str) -> NoReturn:
        """Raise `UsageError` instead of argparse's default `self.exit(2, ...)`.

        Args:
            message: argparse's own description of the usage problem.

        Raises:
            UsageError: Always — this method never returns.
        """
        raise UsageError(f"{message} (see --help)")


def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int:
    """Seed a database with 25 triaged alerts through the production write path.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        llm: An `LLMClient` to use for every alert instead of a fresh `FakeLLMClient` per alert
            — the seam tests inject `FakeLLMClient` through (CONVENTIONS.md §10). Beats `--live`.

    Returns:
        `0` on success (`created=<n> skipped=<n> failed=<n>` printed to stdout — failed triages
        are reported, never fatal); `1` on a usage error, a `Settings()` validation failure, a
        missing database URL, `--live` without `LLM_API_KEY`, a `--live` client construction
        `ConfigError`, an invalid/missing golden file, an invalid/missing fixtures directory, or
        a database error.
    """
    parser = _Parser(prog="seed_dev.py")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return _fail(e.code, str(e))

    try:
        settings = Settings()
    except ValidationError as e:
        return _fail("config_error", str(e))

    database_url = args.database_url
    if database_url is None:
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL") or ""
    if not database_url:
        return _fail("config_error", "no database URL (pass --database-url or set DATABASE_URL)")

    if args.live and not settings.llm_api_key.get_secret_value():
        return _fail("config_error", "--live requires LLM_API_KEY")

    # llm= (tests) beats --live for which client seed() uses; --live alone still selects the
    # cheap_model routing tier (only --live prices verdicts against a real model id).
    client: LLMClient | None = llm
    model = settings.cheap_model if args.live else FAKE_MODEL
    if client is None and args.live:
        try:
            client = OpenAICompatibleLLMClient.from_settings(settings)
        except ConfigError as e:
            return _fail(e.code, str(e))

    # Golden checked before fixtures (check-order items 6, 7): each is validated on its own,
    # before `load_candidates` re-walks both to build the actual (fixtures-first) candidate list,
    # so a failure is unambiguously attributable to the file that caused it.
    try:
        load_golden(args.golden)
    except (ValueError, OSError) as e:
        return _fail("invalid_golden", str(e))

    if not args.fixtures.is_dir():
        return _fail("invalid_fixtures", f"{args.fixtures} is not a directory")
    try:
        for path in sorted(args.fixtures.glob("*.json")):
            SessionAlert.model_validate_json(path.read_text())
    except (ValueError, OSError) as e:
        return _fail("invalid_fixtures", str(e))

    candidates = load_candidates(golden=args.golden, fixtures=args.fixtures)

    try:
        counts = asyncio.run(
            seed(
                candidates,
                database_url=database_url,
                schema=args.schema,
                llm=client,
                model=model,
                prompt_version=settings.triage_prompt_version,
            )
        )
    except (OSError, SQLAlchemyError) as e:
        return _fail("database_error", str(e))

    print(f"created={counts.created} skipped={counts.skipped} failed={counts.failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
