"""`evals.run`: drive the real `TriagePipeline` over a golden set and score it (PRD §7.2, §7.3).

`python -m evals.run --golden <path> --prompt <version> [--prompt <version> ...] [--model ID]
[--strong-model ID] [--concurrency N] [--output-dir DIR] [--tool-fixtures DIR]` runs one
`TriagePipeline` per `--prompt` value over every case in the golden set
(`evals.golden.load_golden`), scores each run (`evals.scoring.score`), and prints one comparable
table row per prompt (`evals.scoring.format_table`) — two different prompt versions in one
invocation is how PRD §12 M1's acceptance criterion is demonstrated. Each run's full per-case
result (including its `tool_calls` count, PRD §7.2, and whether routing escalated it, PRD §6.4,
m5 task-03) is written as JSON under the gitignored `evals/results/`; per `.claude/rules/evals.md`
v1's numbers are synthetic and are **never published** outside that JSON and task/ledger reports —
never `docs/results.md`, the README, or a commit message.

`--strong-model` (default `settings.strong_model`; empty means routing is off, same rule as
`worker.triage.TriagePipeline`) wires two-tier routing into every prompt version's pipeline; the
printed table's `escalation_rate` column reports the fraction of cases each run escalated.

`--tool-fixtures DIR` (default `tests/fixtures/tools`) is where every external tool
(`lookup_ip_reputation`, `get_ip_geo_asn`, `get_alert_history`) replays its result from
(`worker.tools.ReplayToolRecorder`); the LLM is the only live component of an eval run
(`.claude/rules/evals.md`) — local tools (`get_session_commands`, `get_asset_info`) still run.
Only `lookup_ip_reputation`/`get_ip_geo_asn` are recordable/strict (`evals/record.py` can
enumerate every argument set a case can request, keyed on `src_ip` alone); `get_alert_history`'s
`window_hours` is the model's free choice, so its fixture space is unbounded and it always
replays leniently, strict or not (ruling R26, m7 task-02 fix-1).

`--judge`/`--no-judge` (default: on iff `is_v2_golden(args.golden)`, m7 task-03) scores every
successful case's verdict reasoning through `evals.judge.judge_case` (PRD §7.3), over the SAME
replayed tool results that case's own pipeline trace recorded; `--judge-model` (default
`settings.strong_model`) is the model the judgment is requested from. A judge failure never fails
the case's own (already-succeeded) triage result — it is recorded as a `None` judge instead. Judge
spend is accounted separately (`RunMetrics.judge_cost_total_usd`) and never added to
`cost_mean_usd`/`cost_total_usd` (`.claude/rules/evals.md`).

`--replay-strict`/`--no-replay-strict` (default: strict iff `is_v2_golden(args.golden)`, PRD §13
— "a v2 case whose fixture is missing fails the eval loudly rather than going live") applies ONLY
to the two `{"ip"}` tools `evals.record` can enumerate (`worker.tools.STRICT_TOOL_NAMES`, ruling
R26, m7 task-02 fix-1) — `get_alert_history`'s `window_hours` is the model's free choice and keeps
the lenient M4-era degrade even under `--replay-strict`. For a strict-scoped tool, a missing (or
poisoned, ruling R25) fixture raises `core.errors.FixtureMissingError` instead of degrading to
`unavailable(...)`; a case whose pipeline run raises it is captured as
`CaseResult(error="fixture_missing:<tool>:<key>", verdict=None)`, exactly like any other per-case
failure. Whenever any case hit one, the run exits `1` and — taking priority over the
`all_cases_failed` path below even when every case failed this way (ruling I2) — prints the usual
table followed by a `"MISSING FIXTURES (n): <tool> <key> ..."` line naming every distinct missing
fixture across every prompt version run, so a new v2 golden row without its fixtures fails CI
loudly.

Every failure path prints exactly one `error: <code>: <message>` line to stderr, leaves stdout
empty, and never raises a traceback:

    argparse usage error (no --prompt, unknown flag, --concurrency < 1,
        --tool-fixtures not a directory)                                     usage
    `Settings()` fails validation (e.g. malformed MODEL_PRICES_JSON)          config_error
    golden file missing/unreadable/invalid row (`load_golden` raises)         invalid_golden
    a v2 golden file (`is_v2_golden`) carrying a non-human-labeled row
        (PRD §13; ruling R13 — checked separately, AFTER a clean load, so a
        malformed row on a v2 path still reports invalid_golden, not this)     config_error
    `ConfigError` from `from_settings`/`TriagePipeline` (unpriced --model or
        --strong-model, unknown --prompt), raised before any case runs
        ("price before spend")                                                config_error
    `ValueError` from `TriagePipeline` (--strong-model equal to --model)       config_error
    output directory not writable                                            output_error
    every case failed in every prompt run (a per-case failure alone still
        exits 0 -- it is captured as `CaseResult.error`, not a run failure)   all_cases_failed

Evaluation always drives the real `worker.triage.TriagePipeline`, never a reimplementation of it
(`.claude/rules/evals.md`). `UsageError`/`Parser`/`fail` are `core.cli`'s shared CLI presentation
helpers (m5 task-05), not a local copy.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from core.cli import Parser, UsageError, fail
from core.config import Settings
from core.errors import (
    ConfigError,
    FixtureMissingError,
    LLMCallError,
    StructuredOutputError,
    VerdictValidationError,
)
from core.llm import LLMClient
from evals.golden import GoldenCase, load_golden
from evals.judge import JudgeScore, judge_case
from evals.scoring import CaseResult, ResultRow, RunMetrics, format_table, score
from worker.llm_client import OpenAICompatibleLLMClient
from worker.outcome import ToolCallRecord, TriageOutcome
from worker.summarize import summarize_session
from worker.tools import STRICT_TOOL_NAMES, ReplayToolRecorder
from worker.tools.wiring import build_registry
from worker.triage import TriagePipeline

logger = logging.getLogger(__name__)

DEFAULT_TOOL_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tools"


def is_v2_golden(path: Path) -> bool:
    """Whether `path` is a v2 golden file (m7 task-01 Ruling R1: basename starts with `v2`).

    A v2 golden path is loaded with `require_human=True` (PRD §13): a machine-authored row must
    never be scored as ground truth. Shared with task-02's fixture-recording tooling.

    Args:
        path: The golden file path to classify.

    Returns:
        `True` when `path.name` starts with `"v2"` (e.g. `v2.jsonl`, `v2-candidates.jsonl`,
        `v20-not-really-v2.jsonl`); `False` otherwise (e.g. `v1.jsonl`).
    """
    return path.name.startswith("v2")


def _positive_int(value: str) -> int:
    """`argparse` `type=` for `--concurrency`: a value `< 1` is a usage error, not a runtime one.

    `asyncio.Semaphore(concurrency)` raises `ValueError` for a negative value and blocks forever
    for `0`; both must be rejected here, before `args` is even built, so they route through
    `Parser.error` (`UsageError` -> `fail("usage", ...)`) like every other malformed flag, never
    as a traceback or a hang.

    Raises:
        argparse.ArgumentTypeError: `value` is not an int, or is `< 1`. `argparse` converts this
            (and a plain `ValueError` from `int()`) into a usage error via `self.error()`.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"--concurrency must be >= 1, got {parsed}")
    return parsed


def _tool_trace_sha256(tool_calls: Sequence[ToolCallRecord]) -> str:
    """sha256 over the canonical JSON of `tool_calls`' name/arguments/result, in order (m7
    task-02 fix-1, ruling R28) — the "tool evidence" fingerprint two replayed runs must agree on.

    `seq`/`latency_ms` are deliberately excluded: `seq` is redundant with list order and
    `latency_ms` is real wall-clock time, not tool evidence.

    Args:
        tool_calls: The pipeline run's tool-call trace, in the order the calls were made; `()`
            for an empty trace (no tool calls, or a case that failed before any were recorded).

    Returns:
        `sha256(json.dumps([{"name", "arguments", "result"}, ...], sort_keys=True,
        separators=(",", ":"), ensure_ascii=True)).hexdigest()`.
    """
    canonical = json.dumps(
        [
            {"name": call.tool_name, "arguments": call.arguments, "result": call.result}
            for call in tool_calls
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_golden(
    cases: Sequence[GoldenCase],
    *,
    pipeline: TriagePipeline,
    concurrency: int = 4,
    judge_llm: LLMClient | None = None,
    judge_model: str = "",
    judge_prompt_version: str = "",
) -> list[CaseResult]:
    """Run every golden-set case through `pipeline`, bounded by a concurrency semaphore.

    A per-case `VerdictValidationError`/`LLMCallError`/`FixtureMissingError` is captured as
    `CaseResult.error` rather than left to abort the run — a run-stopping exception here would
    throw away every other case's already-incurred spend (`.claude/rules/evals.md`: failed cases
    still count in every scoring denominator). `FixtureMissingError` (m7 task-02, strict replay)
    gets its own `error` shape, `f"{e.code}:{e}"` == `"fixture_missing:<tool>:<key>"` — no space
    after the first colon, unlike the other two — so `main` can parse the tool/key back out to
    build the "MISSING FIXTURES" list without re-deriving them.

    m7 task-03: when `judge_llm` is given, every case whose pipeline run succeeds is scored by
    `evals.judge.judge_case` (PRD §7.3) right after its own verdict, inside the same
    semaphore-guarded task — over the SAME tool results that case's own pipeline trace recorded
    (`TriageOutcome.tool_calls`), never a fresh/live tool call and never another case's trace. A
    judge failure (`StructuredOutputError`/`VerdictValidationError` — the judge's own one retry
    already failed twice) is caught here and recorded as `CaseResult.judge=None`; it never fails
    the case's own already-succeeded triage result.

    Args:
        cases: The golden-set cases to run, in the order results should be returned in.
        pipeline: The real `TriagePipeline` to run every case through (never a reimplementation).
        concurrency: Maximum number of cases running through the pipeline at once.
        judge_llm: The LLM client to score reasoning quality through; `None` (default) disables
            judging — no case is judged, every `CaseResult.judge` reads back `None`.
        judge_model: The model id to request the judgment from; ignored when `judge_llm` is `None`.
        judge_prompt_version: The judge prompt version to load; ignored when `judge_llm` is `None`.

    Returns:
        One `CaseResult` per case, in the same order as `cases`.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _judge(case: GoldenCase, outcome: TriageOutcome) -> tuple[JudgeScore | None, Decimal]:
        if judge_llm is None:
            return None, Decimal("0")
        tool_results = [
            {"tool_name": call.tool_name, "arguments": call.arguments, "result": call.result}
            for call in outcome.tool_calls
        ]
        try:
            judge_outcome = await judge_case(
                judge_llm,
                model=judge_model,
                prompt_version=judge_prompt_version,
                summary=summarize_session(case.alert),
                tool_results=tool_results,
                verdict=outcome.verdict,
            )
        except (StructuredOutputError, VerdictValidationError) as e:
            logger.warning(
                "judge call failed case_id=%s reason=%s", case.case_id, e.__class__.__name__
            )
            return None, Decimal("0")
        return judge_outcome.score, judge_outcome.cost_usd

    async def _run_one(case: GoldenCase) -> CaseResult:
        async with semaphore:
            try:
                outcome = await pipeline.run(case.alert)
            except FixtureMissingError as e:
                return CaseResult(
                    case_id=case.case_id,
                    label=case.label,
                    verdict=None,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=Decimal("0"),
                    latency_ms=0,
                    error=f"{e.code}:{e}",
                    tool_calls=0,
                    tool_trace_sha256=_tool_trace_sha256(()),
                    tags=tuple(case.tags),
                )
            except (VerdictValidationError, LLMCallError) as e:
                # M0: a failed case's tokens/cost/latency are not threaded back out of
                # TriagePipeline.run on failure, so they are unknown here and recorded as 0.
                # M5's routing task is expected to carry partial usage through on failure.
                return CaseResult(
                    case_id=case.case_id,
                    label=case.label,
                    verdict=None,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=Decimal("0"),
                    latency_ms=0,
                    error=f"{e.code}: {e}",
                    tool_calls=0,
                    tool_trace_sha256=_tool_trace_sha256(()),
                    tags=tuple(case.tags),
                )
            judge_score, judge_cost_usd = await _judge(case, outcome)
            return CaseResult(
                case_id=case.case_id,
                label=case.label,
                verdict=outcome.verdict,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=outcome.cost_usd,
                latency_ms=outcome.latency_ms,
                error=None,
                tool_calls=len(outcome.tool_calls),
                escalated=outcome.escalated_model,
                tool_trace_sha256=_tool_trace_sha256(outcome.tool_calls),
                judge=judge_score,
                judge_cost_usd=judge_cost_usd,
                tags=tuple(case.tags),
            )

    return list(await asyncio.gather(*(_run_one(case) for case in cases)))


def _git_sha() -> str:
    """The short git SHA of `HEAD`, or `"unknown"` if it can't be determined.

    Never fatal — a result JSON without a SHA is still useful, and this must not be the reason a
    run fails after real spend already happened.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        )
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def _metrics_payload(metrics: RunMetrics) -> dict[str, Any]:
    """`asdict(metrics)` with every `Decimal` field rendered as its exact string.

    JSON has no decimal type; `Decimal` -> `float` would silently reintroduce the precision
    drift CONVENTIONS.md §7 forbids.
    """
    payload: dict[str, Any] = asdict(metrics)
    for key, value in payload.items():
        if isinstance(value, Decimal):
            payload[key] = str(value)
    return payload


def _case_payload(result: CaseResult) -> dict[str, Any]:
    """`asdict(result)` with `verdict`/`label`/`judge` as plain dicts and `Decimal`s as strings."""
    payload: dict[str, Any] = asdict(result)
    payload["verdict"] = result.verdict.model_dump() if result.verdict is not None else None
    payload["label"] = result.label.model_dump()
    payload["cost_usd"] = str(result.cost_usd)
    payload["judge"] = result.judge.model_dump() if result.judge is not None else None
    payload["judge_cost_usd"] = str(result.judge_cost_usd)
    return payload


async def _run_all(
    args: argparse.Namespace, settings: Settings, *, llm: LLMClient | None, http: httpx.AsyncClient
) -> int:
    """The rest of `main`, once argv is parsed and `Settings()` has validated.

    Runs the whole `--prompt` loop inside ONE coroutine, itself run through ONE `asyncio.run`
    call in `main` (M7): every `TriagePipeline` run and `http.aclose()` share a single event
    loop, rather than `http` crossing several separate `asyncio.run`-created loops. The `finally`
    below guarantees `http.aclose()` on every exit path — every early-return in this function is
    one such exit path.

    Args:
        args: The parsed CLI arguments.
        settings: The validated config surface.
        llm: An `LLMClient` to use instead of building the real one; `None` builds the real one.
        http: The process-lifetime `httpx.AsyncClient` every `TriagePipeline` in the run shares,
            via one call to `worker.tools.wiring.build_registry` above the `--prompt` loop —
            never built once per prompt version.

    Returns:
        `0` on success, `1` on any failure path (see `main`'s own docstring).
    """
    try:
        model: str = args.model if args.model is not None else settings.cheap_model
        strong_model: str | None = (
            args.strong_model if args.strong_model is not None else settings.strong_model
        ) or None
        # m7 task-03: --judge defaults on for a v2-named golden file (PRD §13); --judge-model
        # defaults to the strong tier (Interfaces). The DEFAULT additionally requires a judge
        # model to actually be configured — an unconfigured judge tier must never turn a v2 run
        # that never asked for judging into a crash or a surprise spend; `--judge` passed
        # EXPLICITLY still turns judging on regardless (mirrors `strong_model or None`: an empty
        # id means "this tier is off," the same rule two-tier routing already uses).
        judge_model: str = (
            args.judge_model if args.judge_model is not None else settings.strong_model
        )
        judge_enabled: bool = (
            args.judge
            if args.judge is not None
            else (is_v2_golden(args.golden) and bool(judge_model))
        )

        # Price before spend for the flag itself: a fake never prices, so this only applies to
        # the real client, and it must fail here rather than mid-run inside `TriagePipeline`/
        # `complete_structured` (`worker/llm_client.py`), which price-checks per call, deep
        # inside `run_golden`'s `asyncio.gather` — too late to keep every case from starting.
        # `--strong-model` gets the same check (m5 task-03): an unpriced strong id must never
        # reach a real spend either.
        if llm is None and model not in settings.model_prices_json:
            return fail("config_error", f"model {model!r} has no entry in MODEL_PRICES_JSON")
        if (
            llm is None
            and strong_model is not None
            and strong_model not in settings.model_prices_json
        ):
            return fail("config_error", f"model {strong_model!r} has no entry in MODEL_PRICES_JSON")
        if (
            llm is None
            and judge_enabled
            and judge_model
            and judge_model not in settings.model_prices_json
        ):
            return fail("config_error", f"model {judge_model!r} has no entry in MODEL_PRICES_JSON")

        try:
            cases = load_golden(args.golden)
        except (OSError, ValueError) as e:
            return fail("invalid_golden", str(e))

        # Ruling R13 (review I5): only the "not human-labeled" condition is a config_error for a
        # v2 golden file — a malformed row is caught above and stays invalid_golden either way.
        if is_v2_golden(args.golden):
            for row_number, case in enumerate(cases, start=1):
                if case.labeled_by != "human":
                    return fail("config_error", f"row {row_number} is not human-labeled (PRD §13)")

        try:
            client: LLMClient = (
                llm if llm is not None else OpenAICompatibleLLMClient.from_settings(settings)
            )
        except ConfigError as e:
            return fail(e.code, str(e))

        # Ruling default (m7 task-02): strict iff not overridden AND the golden file is v2-named
        # (PRD §13) — a missing fixture on a v2 case must fail loudly; v1 keeps the M4-era
        # degrade unless the flag says otherwise.
        replay_strict = (
            args.replay_strict if args.replay_strict is not None else is_v2_golden(args.golden)
        )

        # One registry per run (N-M5), over the shared `http` client — never one per prompt
        # version. `strict_tools=STRICT_TOOL_NAMES` is explicit (ruling R26): only the two
        # `{"ip"}` tools a v2 case's `src_ip` lets `evals.record` enumerate are strict;
        # `get_alert_history`'s `window_hours` is the model's free choice and keeps the lenient
        # M4-era degrade even under `strict=True`, so a v2 run never flips exit code depending on
        # which window the model happened to pick.
        registry = build_registry(
            settings,
            recorder=ReplayToolRecorder(
                args.tool_fixtures, strict=replay_strict, strict_tools=STRICT_TOOL_NAMES
            ),
            http=http,
        )

        git_sha = _git_sha()
        rows: list[ResultRow] = []
        any_case_succeeded = False
        missing_fixtures: set[tuple[str, str]] = set()
        for prompt_version in args.prompt:
            try:
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
            except ConfigError as e:
                return fail(e.code, str(e))
            except ValueError as e:
                return fail("config_error", str(e))

            # Captured before the run, not after: this is the run's *start* time, not its finish
            # time — a downstream consumer correlating this JSON against logs or computing
            # elapsed wall-clock time needs the former.
            started_at = datetime.now(UTC)
            try:
                results = await run_golden(
                    cases,
                    pipeline=pipeline,
                    concurrency=args.concurrency,
                    judge_llm=client if judge_enabled else None,
                    judge_model=judge_model,
                    judge_prompt_version=settings.judge_prompt_version,
                )
            except (ConfigError, LLMCallError) as e:
                # Backstop: `_run_one` already captures a per-case `VerdictValidationError`/
                # `LLMCallError` as `CaseResult.error`, so a `ConfigError`/`LLMCallError` should
                # never actually escape `run_golden` today. Mirrors `worker/triage_one.py`'s
                # `asyncio.run(pipeline.run(...))` guard at no cost.
                return fail(e.code, str(e))
            metrics = score(results)
            rows.append(ResultRow(prompt_version=prompt_version, model=model, metrics=metrics))
            if any(r.error is None for r in results):
                any_case_succeeded = True
            for result in results:
                if result.error is not None and result.error.startswith("fixture_missing:"):
                    _, tool_name, key = result.error.split(":", 2)
                    missing_fixtures.add((tool_name, key))

            payload = {
                "prompt_version": prompt_version,
                "model": model,
                "git_sha": git_sha,
                "started_at": started_at.isoformat(),
                "metrics": _metrics_payload(metrics),
                "cases": [_case_payload(r) for r in results],
            }
            filename = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{prompt_version}.json"
            try:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                (args.output_dir / filename).write_text(json.dumps(payload, indent=2))
            except OSError as e:
                return fail("output_error", str(e))

        # I2 (m7 task-02 fix-1): a missing-fixture report takes priority over the
        # all_cases_failed short-circuit below — the ordinary Step-6 situation (no fixtures
        # minted yet, so every v2 case raises FixtureMissingError) must still name every missing
        # pair, not just report "every case failed" with nothing actionable.
        if missing_fixtures:
            print(format_table(rows))
            pairs = " ".join(f"{tool_name} {key}" for tool_name, key in sorted(missing_fixtures))
            print(f"MISSING FIXTURES ({len(missing_fixtures)}): {pairs}")
            return 1

        if not any_case_succeeded:
            return fail("all_cases_failed", "every case failed in every prompt run")

        print(format_table(rows))
        return 0
    finally:
        await http.aclose()


def main(
    argv: Sequence[str] | None = None,
    *,
    llm: LLMClient | None = None,
    http: httpx.AsyncClient | None = None,
) -> int:
    """Score every `--prompt` version against a golden set via the real `TriagePipeline`.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        llm: An `LLMClient` to use instead of building the real one from `Settings()` — the seam
            tests inject `FakeLLMClient` through (CONVENTIONS.md §10).
        http: An `httpx.AsyncClient` to use instead of building the real one — the seam tests
            inject a client through to assert it gets closed (N-M5). `None` builds one timed
            from `settings.abuseipdb_timeout_s`, built (and closed) here rather than per prompt
            version or per tool call.

    Returns:
        `0` on success (the table was printed and every prompt's result JSON was written) with
        no missing fixture under `--replay-strict`; `1` on a usage error, a `Settings()`
        validation failure, an invalid/missing golden file, a `ConfigError` raised before any
        case ran, an unwritable output directory, every case failing across every prompt run
        (`all_cases_failed`), or (m7 task-02) any case hitting a missing fixture under strict
        replay — the table is still printed, followed by one `"MISSING FIXTURES (n): ..."` line —
        see the module docstring's failure-path table. Every OTHER `1` path prints exactly one
        `error: <code>: <message>` line to stderr and leaves stdout empty.
    """
    parser = Parser(prog="python -m evals.run")
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--prompt", action="append", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--strong-model", default=None)
    parser.add_argument("--concurrency", type=_positive_int, default=4)
    parser.add_argument("--replay-strict", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--judge", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/results"))
    parser.add_argument("--tool-fixtures", type=Path, default=DEFAULT_TOOL_FIXTURES)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    if not args.tool_fixtures.is_dir():
        return fail("usage", "--tool-fixtures is not a directory")

    try:
        settings = Settings()
    except ValidationError as e:
        return fail("config_error", str(e))

    http_client = http or httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s)
    return asyncio.run(_run_all(args, settings, llm=llm, http=http_client))


if __name__ == "__main__":
    raise SystemExit(main())
