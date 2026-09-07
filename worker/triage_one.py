"""CLI entrypoint: read one alert, triage it, print the verdict as JSON (PRD §12 M0).

`python -m worker.triage_one <alert.json> [--model MODEL] [--prompt VERSION]` reads `Settings()`
once, for its `--model`/`--prompt` defaults and (when `llm` isn't injected) to build the real
`OpenAICompatibleLLMClient`; runs one `TriagePipeline.run(alert)`; and maps every typed failure to
a clean one-line stderr message and a stable exit code — never a Python traceback
(CONVENTIONS.md §4). `llm=` lets tests inject `FakeLLMClient` (or force the config-error path with
`llm=None`) without a network call.

Exit codes:
    0: success — the JSON verdict envelope is on stdout.
    1: a CLI usage error (missing/unknown argument), the alert file is unreadable/invalid,
       `Settings()` itself fails to parse (e.g. a malformed `MODEL_PRICES_JSON`), or a
       `ConfigError`/`LLMCallError` was raised — including a `ConfigError` raised from inside the
       pipeline run (e.g. `--model` naming a model absent from `MODEL_PRICES_JSON`).
    2: `VerdictValidationError` — the reply failed validation on both PRD §6.5 attempts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from core.config import Settings
from core.errors import ConfigError, LLMCallError, SentinelBriefError, VerdictValidationError
from core.llm import LLMClient
from core.schemas.alert import SessionAlert
from worker.llm_client import OpenAICompatibleLLMClient
from worker.triage import TriagePipeline


class UsageError(SentinelBriefError):
    """CLI-only: raised by `_Parser.error` instead of letting argparse exit the process directly.

    Not a `core.errors` member — a malformed command line is a CLI presentation concern, not a
    domain error any other layer needs to catch.
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


def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int:
    """Triage one alert file and print its verdict as JSON; return the process exit code.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        llm: An `LLMClient` to use instead of building the real one from `Settings()` — the seam
            tests inject `FakeLLMClient` through (CONVENTIONS.md §10).

    Returns:
        `0` on success; `1` on a usage error, an unreadable/invalid alert file, `Settings()`
        itself failing to parse, a `ConfigError`, or a `LLMCallError`; `2` on a
        `VerdictValidationError`.
    """
    try:
        settings = Settings()
    except ValidationError as e:
        return _fail("config_error", str(e))

    parser = _Parser(prog="python -m worker.triage_one")
    parser.add_argument("alert_path")
    parser.add_argument("--model", default=settings.cheap_model)
    parser.add_argument("--prompt", default=settings.triage_prompt_version)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return _fail(e.code, str(e))

    try:
        raw = Path(args.alert_path).read_text()
        alert = SessionAlert.model_validate(json.loads(raw))
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        return _fail("invalid_alert", str(e))

    try:
        if llm is None:
            llm = OpenAICompatibleLLMClient.from_settings(settings)
        pipeline = TriagePipeline(llm=llm, model=args.model, prompt_version=args.prompt)
    except ConfigError as e:
        return _fail(e.code, str(e))

    try:
        outcome = asyncio.run(pipeline.run(alert))
    except (ConfigError, LLMCallError) as e:
        return _fail(e.code, str(e))
    except VerdictValidationError as e:
        last_error = " ".join(e.last_error.split())
        print(
            f"error: {e.code}: failed after {e.attempts} attempts: {last_error}",
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "verdict": outcome.verdict.model_dump(),
                "model": outcome.model,
                "prompt_version": outcome.prompt_version,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "cost_usd": str(outcome.cost_usd),
                "latency_ms": outcome.latency_ms,
                "retried": outcome.retried,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
