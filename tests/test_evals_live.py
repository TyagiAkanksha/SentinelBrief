"""Live acceptance test for PRD §12 M1's headline clause: two prompt versions -> two comparable
result rows in one `evals.run` invocation (m1 task-04).

Marked `@pytest.mark.live` (CONVENTIONS.md §9/§10): excluded from the default `pytest -q` run and
from CI via `addopts = "-m 'not live'"`. Run it explicitly with `LLM_API_KEY`, `CHEAP_MODEL` and
`MODEL_PRICES_JSON` exported: `uv run pytest -m live tests/test_evals_live.py`. No `llm=` kwarg is
passed to `main` — this exercises the real `OpenAICompatibleLLMClient` (via
`OpenAICompatibleLLMClient.from_settings`) end to end, driving the real `worker.triage.
TriagePipeline` over the real `evals/golden/v1.jsonl` set (`.claude/rules/evals.md`: evaluation
never reimplements the pipeline). Per `.claude/rules/evals.md`, golden v1's numbers are never
published outside the per-run JSON, the ledger, and task reports — this test asserts only on the
table's *shape* (row count, prompt-version cells) and the JSON file count, never on any actual
metric value, and never prints the API key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import Settings
from evals.run import main

GOLDEN_V1 = "evals/golden/v1.jsonl"


@pytest.mark.live
def test_live_two_prompt_versions_produce_two_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY not set")

    rc = main(
        [
            "--golden",
            GOLDEN_V1,
            "--prompt",
            "triage-v1",
            "--prompt",
            "triage-v2",
            "--concurrency",
            "4",
            "--output-dir",
            str(tmp_path),
        ]
        # no llm= kwarg: exercises the real OpenAICompatibleLLMClient.from_settings path.
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err

    lines = captured.out.rstrip("\n").splitlines()
    body_rows = lines[2:]  # header line, separator line, then one body row per --prompt
    assert len(body_rows) == 2

    def _prompt_cell(row: str) -> str:
        return row.strip().strip("|").split("|")[0].strip()

    assert _prompt_cell(body_rows[0]) == "triage-v1"
    assert _prompt_cell(body_rows[1]) == "triage-v2"

    written = sorted(tmp_path.glob("*.json"))
    assert len(written) == 2
