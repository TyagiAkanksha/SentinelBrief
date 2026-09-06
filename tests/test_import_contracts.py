"""Gate-as-test: the import-linter contracts in pyproject.toml must all hold.

CONVENTIONS.md §2 declares five `forbidden` contracts (the layering rules). This test
shells out to `uv run lint-imports` with no path arguments so it stays aligned with the
canonical contract definitions in pyproject.toml rather than re-declaring them here.
"""

from __future__ import annotations

import subprocess


def test_lint_imports_clean() -> None:
    """CONVENTIONS.md §2: `uv run lint-imports` must report all contracts kept."""
    proc = subprocess.run(["uv", "run", "lint-imports"], capture_output=True, text=True)
    assert proc.returncode == 0, f"lint-imports failed:\n{proc.stdout}\n{proc.stderr}"
