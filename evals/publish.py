"""`evals.publish`: the JSON-safe metrics view, the `eval_runs` row writer, and the append-only
`docs/results.md` publication helpers (PRD §7.2, §7.3, §7.5; m7 task-04 ruling R35, task-06
rulings R41/R47).

`metrics_payload` moves here from `evals/run.py::_metrics_payload` (now public) so it can be
reused by both the per-run JSON `evals.run` already writes and `write_eval_run_row`'s
`eval_runs.metrics` column, without either reaching into the other's internals.

`metrics_from_payload` (m7 task-05, ruling R43) is the exact inverse of `metrics_payload`:
`evals.gate.load_baseline` depends on it to rebuild a `RunMetrics` from a committed baseline's
JSON; `row_from_artifact` (task-06) reuses it unchanged.

Task-06 adds `render_row`/`render_header`/`append_result_row`/`regenerate_header` (the
`docs/results.md` append-only writer, PRD §7.5 — `docs/results.md` is never rewritten, only
appended to, per `.claude/rules/evals.md`) and `PublishedRun`/`row_from_artifact` (ruling R41:
rebuild a publishable row from a nightly run's per-run JSON artifact, with no LLM call).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConfigError
from core.models.eval_runs import EvalRunRow
from evals.scoring import COLUMNS, PR, ResultRow, RunMetrics, format_table


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


_DECIMAL_FIELDS = ("cost_mean_usd", "cost_p95_usd", "cost_total_usd", "judge_cost_total_usd")


def metrics_from_payload(payload: dict[str, Any]) -> RunMetrics:
    """The exact inverse of `metrics_payload` (m7 task-05, ruling R43).

    Args:
        payload: A JSON-safe metrics dict as produced by `metrics_payload`.

    Returns:
        The `RunMetrics` `payload` was built from: `per_severity`'s string band keys become
        `int`s and its dict values become `PR`; `confusion` becomes a tuple of tuples;
        `category_confusion` is carried through unchanged (already all-`str` keys); every
        `Decimal` field's string is parsed back to `Decimal`; `None` judge fields are preserved.
    """
    data: dict[str, Any] = dict(payload)
    data["per_severity"] = {int(band): PR(**pr) for band, pr in data["per_severity"].items()}
    data["confusion"] = tuple(tuple(row) for row in data["confusion"])
    for key in _DECIMAL_FIELDS:
        data[key] = Decimal(data[key])
    return RunMetrics(**data)


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


RESULTS_PATH = Path("docs/results.md")
"""The one published, append-only results table (PRD §7.5); `evals.run` imports this name so a
test can monkeypatch `evals.run.RESULTS_PATH` without ever touching the real file."""


def render_header() -> str:
    """`docs/results.md`'s table header (PRD §7.5): `date, git_sha, prompt_version, models` then
    every `evals.scoring.COLUMNS` metric column (`COLUMNS[0]`/`COLUMNS[1]` — the single-run-row
    `"prompt"`/`"model"` — are replaced, since a published row names a date/sha/model pair rather
    than one bare run).

    Returns:
        One `| … |` markdown header line, the same line `append_result_row` refuses to append
        below unless it still matches.
    """
    return "| " + " | ".join(("date", "git_sha", "prompt_version", "models", *COLUMNS[2:])) + " |"


def render_row(row: ResultRow, *, date: date, git_sha: str, models: str) -> str:
    """One `docs/results.md` table row for `row` (PRD §7.5).

    Reuses `evals.scoring.format_table`'s own per-column formatting (2dp rates, 6dp costs, plain
    latencies, `"-"` for a `None` judge/injection field) rather than re-deriving it, so a
    published row is byte-identical to a `format_table` body row's metric cells — only the
    leading `prompt`/`model` cells are replaced by `date, git_sha, prompt_version, models`.

    Args:
        row: The prompt/model combination's scored metrics.
        date: The date to publish this row under.
        git_sha: The git SHA the run was executed at.
        models: The model cell, already rendered by the caller (`evals.run`) from its effective
            config — `"<model>"` un-escalated, `"<model>→<strong>"` (U+2192) when a strong model
            routed. Never re-derived here.

    Returns:
        One `| … |` markdown row line, in `render_header()`'s column order.
    """
    body_line = format_table([row]).splitlines()[2]
    metric_cells = [cell.strip() for cell in body_line.strip().strip("|").split("|")][2:]
    cells = [date.isoformat(), git_sha, row.prompt_version, models, *metric_cells]
    return "| " + " | ".join(cells) + " |"


def append_result_row(path: Path, line: str) -> None:
    """Append `line` to `path`'s `## Runs` table, after its last existing row (PRD §7.5:
    `docs/results.md` is append-only — a prior line is never rewritten or removed).

    Args:
        path: The results file to append to (`RESULTS_PATH` in production; a tmp file in tests —
            this function never assumes a real, tracked file).
        line: One `render_row(...)`-shaped markdown table row.

    Raises:
        ValueError: `path` has no `## Runs` section, or its header row does not equal
            `render_header()` (column drift — publishing under a stale header would corrupt the
            table). Either way, `path` is left byte-identical; nothing is written on refusal.
    """
    lines = path.read_text().splitlines()
    runs_idx = next((i for i, ln in enumerate(lines) if ln.strip() == "## Runs"), None)
    if runs_idx is None:
        raise ValueError(f"{path}: no '## Runs' section — cannot locate the results table")
    header_line = next((ln for ln in lines[runs_idx:] if ln.strip().startswith("|")), None)
    if header_line != render_header():
        raise ValueError(
            f"{path}: header row {header_line!r} != render_header() {render_header()!r} "
            "(column drift — regenerate the header before publishing)"
        )
    header_idx = lines.index(header_line, runs_idx)
    insert_idx = header_idx + 2  # header, separator, then the first existing row (if any)
    while insert_idx < len(lines) and lines[insert_idx].strip().startswith("|"):
        insert_idx += 1  # skip every already-published row -- append after the LAST one
    new_lines = lines[:insert_idx] + [line] + lines[insert_idx:]
    path.write_text("\n".join(new_lines) + "\n")


def regenerate_header(path: Path) -> None:
    """Replace only `path`'s header + separator lines with `render_header()`'s (PRD §7.5); every
    row and every line of prose is byte-preserved.

    Used once whenever `COLUMNS` grows (m7 task-04/task-06) — never by the ordinary publish path,
    which refuses instead of silently regenerating on drift (`append_result_row`).

    Args:
        path: The results file whose header/separator to replace.
    """
    lines = path.read_text().splitlines()
    runs_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "## Runs")
    header_idx = next(i for i in range(runs_idx, len(lines)) if lines[i].strip().startswith("|"))
    separator_idx = header_idx + 1
    # date, git_sha, prompt_version, models + COLUMNS[2:] -- same cell count as render_header().
    new_separator = "|" + "|".join("---" for _ in range(len(COLUMNS) + 2)) + "|"
    new_lines = lines[:header_idx] + [render_header(), new_separator] + lines[separator_idx + 1 :]
    path.write_text("\n".join(new_lines) + "\n")


def _is_v2_golden(path: Path) -> bool:
    """Whether `path` is a v2 golden file (m7 task-01 ruling R1: basename starts with `v2`).

    A byte-for-byte duplicate of `evals.run.is_v2_golden`'s one-line check, not an import of it:
    `evals.run` already imports this module (`metrics_payload`, `write_eval_run_row`,
    `RESULTS_PATH`, …), so importing `evals.run` from here would be a cycle.
    """
    return path.name.startswith("v2")


@dataclass(frozen=True)
class PublishedRun:
    """A `docs/results.md` row rebuilt from a nightly run's per-run JSON artifact (ruling R41)."""

    row: ResultRow
    date: date
    git_sha: str
    models: str


def row_from_artifact(path: Path) -> PublishedRun:
    """Rebuild a `PublishedRun` from a per-run JSON artifact `evals.run` already wrote, with no
    LLM call (ruling R41 — `evals.run --publish --from-artifact PATH`).

    Args:
        path: The per-run JSON artifact's path (`evals.run`'s `--output-dir` shape).

    Returns:
        The `PublishedRun` to hand to `render_row`/`append_result_row`.

    Raises:
        ConfigError: the artifact's `"golden"` field is not a v2 golden path — v1 numbers are
            never published, even via `--from-artifact` (`.claude/rules/evals.md`).
        ValueError: the artifact is not valid JSON, or is missing a required key.
        OSError: `path` does not exist or is not readable.
    """
    payload: dict[str, Any] = json.loads(path.read_text())
    golden_path = Path(payload["golden"])
    if not _is_v2_golden(golden_path):
        raise ConfigError(
            f"{path}: golden set {golden_path} is not v2 — v1 numbers are never published "
            "(.claude/rules/evals.md)"
        )
    metrics = metrics_from_payload(payload["metrics"])
    row = ResultRow(
        prompt_version=payload["prompt_version"], model=payload["model"], metrics=metrics
    )
    started_at = datetime.fromisoformat(payload["started_at"])
    return PublishedRun(
        row=row, date=started_at.date(), git_sha=payload["git_sha"], models=payload["model"]
    )
