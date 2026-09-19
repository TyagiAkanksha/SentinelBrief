"""Gate-as-test: the import-linter contracts in pyproject.toml must all hold.

CONVENTIONS.md §2 declares five `forbidden` contracts (the layering rules). This test
shells out to `uv run lint-imports` with no path arguments so it stays aligned with the
canonical contract definitions in pyproject.toml rather than re-declaring them here.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


def test_lint_imports_clean() -> None:
    """CONVENTIONS.md §2: `uv run lint-imports` must report all contracts kept."""
    proc = subprocess.run(["uv", "run", "lint-imports"], capture_output=True, text=True)
    assert proc.returncode == 0, f"lint-imports failed:\n{proc.stdout}\n{proc.stderr}"


def test_api_does_not_import_evals() -> None:
    """Ruling R53 (whole-branch fix wave, finding I2): `evals` builds and calls the real LLM
    client, so the `api` layering contract must forbid `evals` alongside `worker`/`core.llm` (PRD
    §10.1) -- otherwise an `api` module importing `evals.golden` (which today reaches only
    `core.schemas`) would pass every contract. `test_lint_imports_clean` proves the contract holds;
    this proves the forbidden set actually names `evals`, so a future edit that drops it fails
    here rather than silently reopening the hole."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    contracts = pyproject["tool"]["importlinter"]["contracts"]
    api_contract = next(c for c in contracts if c["source_modules"] == ["api"])
    assert "evals" in api_contract["forbidden_modules"]
    assert "worker" in api_contract["forbidden_modules"]
    assert "core.llm" in api_contract["forbidden_modules"]
