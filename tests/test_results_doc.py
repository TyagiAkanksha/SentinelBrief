"""Pins `docs/results.md`'s empty results table header against `evals.scoring.COLUMNS` (m1
task-04).

PRD §7.5 / `.claude/rules/evals.md`: every published run row carries `date, git_sha,
prompt_version, models` plus every PRD §7.3 metric column. The metric columns themselves are not
re-invented here — they are `evals.scoring.COLUMNS[2:]` (`COLUMNS[0]`/`COLUMNS[1]`, `"prompt"` and
`"model"`, are the single-run-row shape `format_table` uses; `docs/results.md`'s table instead
carries `prompt_version` and the plural `models`, since a published row can name more than one
model across the two routing tiers). `docs/results.md` today ships only the publication-policy
prose and a "## Runs" placeholder sentence — no table — so this test is RED until the implementer
adds the empty table with this exact header (task-04 Step 5).
"""

from __future__ import annotations

from pathlib import Path

from evals.scoring import COLUMNS

RESULTS_PATH = Path("docs/results.md")


def test_results_table_header_matches_columns() -> None:
    text = RESULTS_PATH.read_text()

    runs_idx = text.index("## Runs")
    body = text[runs_idx:]

    header_line = next((line for line in body.splitlines() if line.strip().startswith("|")), None)
    assert header_line is not None, "no markdown table found under '## Runs' in docs/results.md"

    cells = [cell.strip() for cell in header_line.strip().strip("|").split("|")]
    assert cells == ["date", "git_sha", "prompt_version", "models", *COLUMNS[2:]]
