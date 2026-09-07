"""Live smoke test for `worker.triage_one.main` (m0 task-05 / PRD §12 M0).

Marked `@pytest.mark.live` (CONVENTIONS.md §9/§10): excluded from the default `pytest -q` run and
from CI via `addopts = "-m 'not live'"`. Run it explicitly with `LLM_API_KEY`, `CHEAP_MODEL` and
`MODEL_PRICES_JSON` exported: `uv run pytest -m live tests/test_triage_live.py`. Never asserts on
or prints the API key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config import Settings
from worker.triage_one import main

FIXTURE_PATHS = sorted(Path("fixtures/alerts").glob("alert*.json"))


@pytest.mark.live
def test_live_triage_all_fixtures(capsys: pytest.CaptureFixture[str]) -> None:
    """Each of the five fixtures triages end to end against the real, configured LLM."""
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("LLM_API_KEY not set")

    for fixture_path in FIXTURE_PATHS:
        rc = main([str(fixture_path)])
        captured = capsys.readouterr()
        assert rc == 0, captured.err
        out = json.loads(captured.out)
        assert 1 <= out["verdict"]["severity"] <= 5
