"""AI labeler for the golden set v2 — the ONLY place in the repo that writes the ``"ai"`` label
provenance (`GoldenCase.labeled_by == "ai"`).

Owner decision 2026-09-18 (ruling R54): the v2 golden set is labeled by a strong model instead of
by a person, and every published surface (`docs/results.md`, `/about`, the golden README) discloses
this and its self-grading limitation. This module never writes the human provenance value — a
person labeling blind is `evals.label_tool`'s job, and the AST/grep guards in
`tests/test_golden_v2.py` / `tests/test_label_tool.py` keep that literal confined there.

Method (honest and deliberately reproducible): each candidate session is run through the SAME
tested `worker.triage.TriagePipeline` used everywhere else, but on the STRONG model tier, and the
resulting verdict's `severity`/`category`/`escalate` become the label. The eval then measures the
configured (cheap-tier) triage against strong-tier labels — a model-tier agreement metric, not
human ground truth. Because labeler and triage share a provider family, the numbers are disclosed
as machine-labeled wherever they are published (PRD §7.5).

CLI:
    uv run python -m evals.ai_label --candidates evals/golden/v2-candidates.jsonl \
        --out evals/golden/v2.jsonl [--model STRONG_ID] [--concurrency 4]

Appends one `labeled_by="ai"` `GoldenCase` per candidate to ``--out`` (created if absent), skipping
any candidate whose `case_id` is already present, so a run is resumable and two candidate files
fold into one output. A candidate the pipeline cannot triage (a validation or LLM-call failure) is
skipped with a one-line stderr note (its `case_id` only, never its content) and NOT written — an
unlabeled row must never reach the golden file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config import Settings
from core.errors import ConfigError, LLMCallError, VerdictValidationError
from core.llm import LLMClient
from core.schemas.alert import SessionAlert
from evals.candidates import matches_injection_hint
from evals.golden import GoldenCase, GoldenLabel, load_golden
from worker.llm_client import OpenAICompatibleLLMClient
from worker.triage import TriagePipeline


def _load_candidate_alerts(path: Path) -> list[tuple[str, SessionAlert]]:
    """Read `evals.sample.write_candidates` output into `(case_id, alert)` pairs.

    Guarded like `evals.label_tool._load_candidates` (review N1): a row that is not a JSON object
    or is missing `case_id`/`alert` raises `ValueError` naming the row number and the missing key
    only — never the row's own (possibly attacker-derived) content.
    """
    pairs: list[tuple[str, SessionAlert]] = []
    for row_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row: Any = json.loads(line)
        except json.JSONDecodeError as err:
            raise ValueError(f"row {row_number}: not valid JSON") from err
        if not isinstance(row, dict):
            raise ValueError(f"row {row_number}: not a JSON object")
        if "case_id" not in row:
            raise ValueError(f"row {row_number}: missing key 'case_id'")
        if "alert" not in row:
            raise ValueError(f"row {row_number}: missing key 'alert'")
        pairs.append((str(row["case_id"]), SessionAlert.model_validate(row["alert"])))
    return pairs


def _note(model: str, prompt_version: str) -> str:
    """The disclosed AI-labeling provenance note (min_length 10; never the human literal)."""
    today = datetime.now(UTC).date().isoformat()
    return (
        f"AI-labeled by {model} via the triage pipeline (prompt {prompt_version}) on {today} — "
        "machine ground truth for a model-tier agreement metric, not a person's judgment (R54)."
    )


async def label_candidates(
    alerts: Sequence[tuple[str, SessionAlert]],
    *,
    pipeline: TriagePipeline,
    model: str,
    prompt_version: str,
    already_labeled: set[str],
    out_path: Path,
    concurrency: int = 4,
) -> int:
    """Label every not-yet-labeled candidate through `pipeline`, appending to `out_path`.

    Returns the number of rows newly written. A candidate whose pipeline run fails is skipped
    (stderr note, `case_id` only) and never written — the golden file gains no unlabeled row.
    """
    semaphore = asyncio.Semaphore(concurrency)
    note = _note(model, prompt_version)

    async def _label_one(case_id: str, alert: SessionAlert) -> GoldenCase | None:
        async with semaphore:
            try:
                outcome = await pipeline.run(alert)
            except (VerdictValidationError, LLMCallError) as e:
                print(f"skipped {case_id}: {type(e).__name__}", file=sys.stderr)
                return None
        verdict = outcome.verdict
        return GoldenCase(
            alert=alert,
            label=GoldenLabel(
                severity=verdict.severity,
                category=verdict.category,
                escalate=verdict.escalate,
            ),
            labeler_note=note,
            tags=["injection"] if matches_injection_hint(alert) else [],
            labeled_by="ai",
            labeled_at=datetime.now(UTC),
        )

    pending = [(cid, alert) for cid, alert in alerts if cid not in already_labeled]
    results = await asyncio.gather(*(_label_one(cid, alert) for cid, alert in pending))

    written = 0
    if out_path.exists():
        existing = out_path.read_text()
        if existing and not existing.endswith("\n"):
            with out_path.open("a") as f:
                f.write("\n")
    with out_path.open("a") as f:
        for case in results:
            if case is None:
                continue
            f.write(case.model_dump_json() + "\n")
            f.flush()
            written += 1
    return written


def main(argv: Sequence[str] | None = None, *, llm: LLMClient | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.ai_label", description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=None, help="labeler model id (default: STRONG_MODEL)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)

    settings = Settings()
    model = args.model if args.model is not None else settings.strong_model
    if not model:
        print(
            "error: config_error: no labeler model (set STRONG_MODEL or --model)", file=sys.stderr
        )
        return 1

    try:
        alerts = _load_candidate_alerts(args.candidates)
    except (OSError, ValueError) as e:
        print(f"error: io_error: {e}", file=sys.stderr)
        return 1

    already_labeled: set[str] = set()
    if args.out.exists() and args.out.read_text().strip():
        already_labeled = {case.case_id for case in load_golden(args.out)}

    try:
        client = llm if llm is not None else OpenAICompatibleLLMClient.from_settings(settings)
        pipeline = TriagePipeline(
            llm=client, model=model, prompt_version=settings.triage_prompt_version
        )
    except ConfigError as e:
        print(f"error: {e.code}: {e}", file=sys.stderr)
        return 1

    written = asyncio.run(
        label_candidates(
            alerts,
            pipeline=pipeline,
            model=model,
            prompt_version=settings.triage_prompt_version,
            already_labeled=already_labeled,
            out_path=args.out,
            concurrency=args.concurrency,
        )
    )
    print(f"ai-labeled {written} candidate(s) → {args.out} (labeled_by=ai)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
