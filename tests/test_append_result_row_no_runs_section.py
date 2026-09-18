"""`evals.publish.append_result_row` refuses a results file with no `## Runs` section (m7 task-06
implementer addition, alongside the test-author's pinned `tests/test_results_publish.py`).

Every other `append_result_row`/`render_row`/`render_header`/`regenerate_header`/`row_from_artifact`
behavior is pinned by `tests/test_results_publish.py`; this one extra branch (a results file
missing its `"## Runs"` heading entirely, as opposed to a header that drifted from
`render_header()`) is not exercised there, so it is added here rather than left uncovered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.publish import append_result_row


def test_append_refuses_when_no_runs_section(tmp_path: Path) -> None:
    path = tmp_path / "results.md"
    original = "# Evaluation results\n\nno '## Runs' heading anywhere in this file.\n"
    path.write_text(original)

    with pytest.raises(ValueError):
        append_result_row(path, "| a row |")

    assert path.read_text() == original  # a refused append never touches the file
