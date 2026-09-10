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

Every failure path prints exactly one `error: <code>: <message>` line to stderr, leaves stdout
empty, and never raises a traceback:

    argparse usage error (no --prompt, unknown flag, --concurrency < 1,
        --tool-fixtures not a directory)                                     usage
    `Settings()` fails validation (e.g. malformed MODEL_PRICES_JSON)          config_error
    golden file missing/unreadable/invalid row (`load_golden` raises)         invalid_golden
    `ConfigError` from `from_settings`/`TriagePipeline` (unpriced --model or
        --strong-model, unknown --prompt), raised before any case runs
        ("price before spend")                                                config_error
    `ValueError` from `TriagePipeline` (--strong-model equal to --model)       config_error
    output directory not writable                                            output_error
    every case failed in every prompt run (a per-case failure alone still
        exits 0 -- it is captured as `CaseResult.error`, not a run failure)   all_cases_failed

Evaluation always drives the real `worker.triage.TriagePipeline`, never a reimplementation of it
(`.claude/rules/evals.md`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import httpx
from pydantic import ValidationError

from core.config import Settings
from core.errors import ConfigError, LLMCallError, SentinelBriefError, VerdictValidationError
from core.llm import LLMClient
from evals.golden import GoldenCase, load_golden
from evals.scoring import CaseResult, ResultRow, RunMetrics, format_table, score
from worker.llm_client import OpenAICompatibleLLMClient
from worker.tools import ReplayToolRecorder
from worker.tools.wiring import build_registry
from worker.triage import TriagePipeline

DEFAULT_TOOL_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tools"


class UsageError(SentinelBriefError):
    """CLI-only: raised by `_Parser.error` instead of letting argparse exit the process directly.

    Not a `core.errors` member — a malformed command line is a CLI presentation concern, not a
    domain error any other layer needs to catch (mirrors `worker/triage_one.py::UsageError`).
    """

    code = "usage"


class _Parser(argparse.ArgumentParser):
    """An `ArgumentParser` that raises `UsageError` on a usage error instead of exiting.

    `--help` is unaffected: it exits via `self.exit(0, ...)` in argparse's own help action, which
    never calls `error()`.
    """

    def error(self, message: str) -> NoReturn:
        """Raise `UsageError` instead of argparse's default `self.exit(2, ...)`.

        Args:
            message: argparse's own description of the usage problem.

        Raises:
            UsageError: Always — this method never returns.
        """
        raise UsageError(f"{message} (see --help)")


def _positive_int(value: str) -> int:
    """`argparse` `type=` for `--concurrency`: a value `< 1` is a usage error, not a runtime one.

    `asyncio.Semaphore(concurrency)` raises `ValueError` for a negative value and blocks forever
    for `0`; both must be rejected here, before `args` is even built, so they route through
    `_Parser.error` (`UsageError` -> `_fail("usage", ...)`) like every other malformed flag,
    never as a traceback or a hang.

    Raises:
        argparse.ArgumentTypeError: `value` is not an int, or is `< 1`. `argparse` converts this
            (and a plain `ValueError` from `int()`) into a usage error via `self.error()`.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"--concurrency must be >= 1, got {parsed}")
    return parsed


def _fail(code: str, message: str) -> int:
    """Print one flattened `error: <code>: <message>` line to stderr; never a traceback.

    Args:
        code: The stable wire code for this failure (e.g. `"config_error"`).
        message: A human-readable description; embedded newlines are collapsed so the line
            stays exactly one line, matching every other error path's shape.

    Returns:
        Always `1` — every caller of this helper is a `1`-exit-code path.
    """
    print(f"error: {code}: {' '.join(message.split())}", file=sys.stderr)
    return 1


async def run_golden(
    cases: Sequence[GoldenCase], *, pipeline: TriagePipeline, concurrency: int = 4
) -> list[CaseResult]:
    """Run every golden-set case through `pipeline`, bounded by a concurrency semaphore.

    A per-case `VerdictValidationError`/`LLMCallError` is captured as `CaseResult.error` rather
    than left to abort the run — a run-stopping exception here would throw away every other
    case's already-incurred spend (`.claude/rules/evals.md`: failed cases still count in every
    scoring denominator).

    Args:
        cases: The golden-set cases to run, in the order results should be returned in.
        pipeline: The real `TriagePipeline` to run every case through (never a reimplementation).
        concurrency: Maximum number of cases running through the pipeline at once.

    Returns:
        One `CaseResult` per case, in the same order as `cases`.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(case: GoldenCase) -> CaseResult:
        async with semaphore:
            try:
                outcome = await pipeline.run(case.alert)
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
                )
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
    """`asdict(result)` with `verdict`/`label` as plain dicts and `cost_usd` as a string."""
    payload: dict[str, Any] = asdict(result)
    payload["verdict"] = result.verdict.model_dump() if result.verdict is not None else None
    payload["label"] = result.label.model_dump()
    payload["cost_usd"] = str(result.cost_usd)
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

        # Price before spend for the flag itself: a fake never prices, so this only applies to
        # the real client, and it must fail here rather than mid-run inside `TriagePipeline`/
        # `complete_structured` (`worker/llm_client.py`), which price-checks per call, deep
        # inside `run_golden`'s `asyncio.gather` — too late to keep every case from starting.
        # `--strong-model` gets the same check (m5 task-03): an unpriced strong id must never
        # reach a real spend either.
        if llm is None and model not in settings.model_prices_json:
            return _fail("config_error", f"model {model!r} has no entry in MODEL_PRICES_JSON")
        if (
            llm is None
            and strong_model is not None
            and strong_model not in settings.model_prices_json
        ):
            return _fail(
                "config_error", f"model {strong_model!r} has no entry in MODEL_PRICES_JSON"
            )

        try:
            cases = load_golden(args.golden)
        except (ValueError, OSError) as e:
            return _fail("invalid_golden", str(e))

        try:
            client: LLMClient = (
                llm if llm is not None else OpenAICompatibleLLMClient.from_settings(settings)
            )
        except ConfigError as e:
            return _fail(e.code, str(e))

        # One registry per run (N-M5), over the shared `http` client — never one per prompt
        # version.
        registry = build_registry(
            settings, recorder=ReplayToolRecorder(args.tool_fixtures), http=http
        )

        git_sha = _git_sha()
        rows: list[ResultRow] = []
        any_case_succeeded = False
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
                return _fail(e.code, str(e))
            except ValueError as e:
                return _fail("config_error", str(e))

            # Captured before the run, not after: this is the run's *start* time, not its finish
            # time — a downstream consumer correlating this JSON against logs or computing
            # elapsed wall-clock time needs the former.
            started_at = datetime.now(UTC)
            try:
                results = await run_golden(cases, pipeline=pipeline, concurrency=args.concurrency)
            except (ConfigError, LLMCallError) as e:
                # Backstop: `_run_one` already captures a per-case `VerdictValidationError`/
                # `LLMCallError` as `CaseResult.error`, so a `ConfigError`/`LLMCallError` should
                # never actually escape `run_golden` today. Mirrors `worker/triage_one.py`'s
                # `asyncio.run(pipeline.run(...))` guard at no cost.
                return _fail(e.code, str(e))
            metrics = score(results)
            rows.append(ResultRow(prompt_version=prompt_version, model=model, metrics=metrics))
            if any(r.error is None for r in results):
                any_case_succeeded = True

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
                return _fail("output_error", str(e))

        if not any_case_succeeded:
            return _fail("all_cases_failed", "every case failed in every prompt run")

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
        `0` on success (the table was printed and every prompt's result JSON was written); `1`
        on a usage error, a `Settings()` validation failure, an invalid/missing golden file, a
        `ConfigError` raised before any case ran, an unwritable output directory, or when every
        case failed across every prompt run (`all_cases_failed`) — see the module docstring's
        failure-path table. Every `1` path prints exactly one `error: <code>: <message>` line to
        stderr and leaves stdout empty.
    """
    parser = _Parser(prog="python -m evals.run")
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--prompt", action="append", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--strong-model", default=None)
    parser.add_argument("--concurrency", type=_positive_int, default=4)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/results"))
    parser.add_argument("--tool-fixtures", type=Path, default=DEFAULT_TOOL_FIXTURES)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return _fail(e.code, str(e))

    if not args.tool_fixtures.is_dir():
        return _fail("usage", "--tool-fixtures is not a directory")

    try:
        settings = Settings()
    except ValidationError as e:
        return _fail("config_error", str(e))

    http_client = http or httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s)
    return asyncio.run(_run_all(args, settings, llm=llm, http=http_client))


if __name__ == "__main__":
    raise SystemExit(main())
