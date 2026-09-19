"""CLI entrypoint: read one alert, triage it, print the verdict as JSON (PRD §12 M0).

`python -m worker.triage_one <alert.json> [--model MODEL] [--prompt VERSION]
[--strong-model MODEL]` reads `Settings()` once, for its `--model`/`--prompt`/`--strong-model`
defaults and (when `llm` isn't injected) to build the real `OpenAICompatibleLLMClient`; runs one
`TriagePipeline.run(alert)`; and maps every typed failure to a clean one-line stderr message and a
stable exit code — never a Python traceback (CONVENTIONS.md §4). `llm=` lets tests inject
`FakeLLMClient` (or force the config-error path with `llm=None`) without a network call.
`UsageError`/`Parser`/`fail` are `core.cli`'s shared CLI presentation helpers (m5 task-05), not a
local copy.

`--strong-model` (default `settings.strong_model`; empty disables routing, PRD §6.4, m5 task-03)
wires two-tier routing; the printed JSON always carries `model_primary`/`escalated_model`, `false`
when routing never fired.

Exit codes:
    0: success — the JSON verdict envelope is on stdout.
    1: a CLI usage error (missing/unknown argument), the alert file is unreadable/invalid,
       `Settings()` itself fails to parse (e.g. a malformed `MODEL_PRICES_JSON`), `--strong-model`
       naming a model absent from `MODEL_PRICES_JSON` (checked up front, before any client is
       built — "price before spend", m5 task-03 fix-1), or a `ConfigError`/`LLMCallError` was
       raised — including a `ConfigError` raised from inside the pipeline run (e.g. `--model`
       naming a model absent from `MODEL_PRICES_JSON`).
    2: `VerdictValidationError` — the reply failed validation on both PRD §6.5 attempts.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from core.cli import Parser, UsageError, fail
from core.config import Settings
from core.errors import ConfigError, LLMCallError, VerdictValidationError
from core.llm import LLMClient
from core.schemas.alert import SessionAlert
from worker.llm_client import OpenAICompatibleLLMClient
from worker.triage import TriagePipeline


def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int:
    """Triage one alert file and print its verdict as JSON; return the process exit code.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        llm: An `LLMClient` to use instead of building the real one from `Settings()` — the seam
            tests inject `FakeLLMClient` through (CONVENTIONS.md §10).

    Returns:
        `0` on success; `1` on a usage error, an unreadable/invalid alert file, `Settings()`
        itself failing to parse, a `ConfigError`, a `ValueError` from `TriagePipeline` (e.g.
        `--strong-model` equal to `--model`), or a `LLMCallError`; `2` on a
        `VerdictValidationError`.
    """
    try:
        settings = Settings()
    except ValidationError as e:
        return fail("config_error", str(e))

    parser = Parser(prog="python -m worker.triage_one")
    parser.add_argument("alert_path")
    parser.add_argument("--model", default=settings.cheap_model)
    parser.add_argument("--prompt", default=settings.triage_prompt_version)
    parser.add_argument("--strong-model", default=settings.strong_model)
    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    # Price before spend for --strong-model (m5 task-03 fix-1, review M3), mirroring
    # `evals/run.py`: only on the real-client path, before any client (or the cheap tier's own
    # network call) is built — an unpriced strong id must never reach a real spend either.
    if llm is None and args.strong_model and args.strong_model not in settings.model_prices_json:
        return fail(
            "config_error", f"model {args.strong_model!r} has no entry in MODEL_PRICES_JSON"
        )

    try:
        raw = Path(args.alert_path).read_text()
        alert = SessionAlert.model_validate(json.loads(raw))
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        return fail("invalid_alert", str(e))

    try:
        if llm is None:
            llm = OpenAICompatibleLLMClient.from_settings(settings)
        pipeline = TriagePipeline(
            llm=llm,
            model=args.model,
            prompt_version=args.prompt,
            strong_model=args.strong_model or None,
            escalate_severity_gte=settings.escalate_severity_gte,
            escalate_confidence_lt=settings.escalate_confidence_lt,
        )
    except (ConfigError, ValueError) as e:
        return fail("config_error", str(e))

    try:
        outcome = asyncio.run(pipeline.run(alert))
    except (ConfigError, LLMCallError) as e:
        return fail(e.code, str(e))
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
                "model_primary": outcome.effective_model_primary,
                "escalated_model": outcome.escalated_model,
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
