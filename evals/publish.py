"""`evals.publish`: the JSON-safe metrics view and the `eval_runs` row writer (PRD §7.2, §7.3;
m7 task-04, ruling R35).

`metrics_payload` moves here from `evals/run.py::_metrics_payload` (now public) so it can be
reused by both the per-run JSON `evals.run` already writes and `write_eval_run_row`'s
`eval_runs.metrics` column, without either reaching into the other's internals. Task-06 extends
this module with the `docs/results.md` append helper — nothing defined here moves again.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.models.eval_runs import EvalRunRow
from evals.scoring import RunMetrics


def metrics_payload(metrics: RunMetrics) -> dict[str, Any]:
    """`asdict(metrics)` with every `Decimal` field rendered as its exact string (PRD §7.2/§7.3).

    JSON has no decimal type; `Decimal` -> `float` would silently reintroduce the precision drift
    CONVENTIONS.md §7 forbids. `per_severity`'s integer band keys become strings (ruling R35 —
    JSON object keys are always strings); `confusion` (already a tuple of tuples) and
    `category_confusion` round-trip through `asdict` as plain lists/dicts unchanged.

    Args:
        metrics: The run's aggregated metrics.

    Returns:
        A JSON-safe dict: every `Decimal` as its `str()`, `per_severity` keyed by string band.
    """
    payload: dict[str, Any] = asdict(metrics)
    payload["per_severity"] = {str(band): pr for band, pr in payload["per_severity"].items()}
    for key, value in payload.items():
        if isinstance(value, Decimal):
            payload[key] = str(value)
    return payload


async def write_eval_run_row(
    session: AsyncSession,
    *,
    git_sha: str | None,
    prompt_version: str,
    model_config: dict[str, Any],
    started_at: datetime,
    metrics: RunMetrics,
) -> uuid.UUID:
    """Insert one `eval_runs` row for a finished run (PRD §7.2: "writes an `eval_runs` row").

    `flush()` only, never `commit()` — CONVENTIONS.md §3: the transaction boundary belongs to the
    caller (`evals.run`'s `--database-url` path commits once per prompt version).

    Args:
        session: The session to insert into; the caller owns the transaction.
        git_sha: The git SHA the run was executed at; `None` when it couldn't be determined.
        prompt_version: The prompt version this run scored.
        model_config: The run's effective model configuration (PRD §7.2: "the effective model
            configuration is recorded in eval_runs.model_config").
        started_at: When this prompt version's run started.
        metrics: The run's aggregated metrics, serialized via `metrics_payload`.

    Returns:
        The new row's id.
    """
    row = EvalRunRow(
        git_sha=git_sha,
        prompt_version=prompt_version,
        model_config=model_config,
        started_at=started_at,
        metrics=metrics_payload(metrics),
    )
    session.add(row)
    await session.flush()
    return row.id
