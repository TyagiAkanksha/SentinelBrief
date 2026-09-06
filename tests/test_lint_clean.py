"""Gates-as-tests: ruff check, ruff format, and mypy strict must be clean.

Each test shells out to the exact gate command from CONVENTIONS.md §9, with no path
arguments, so the test stays aligned with the canonical tool config in pyproject.toml
rather than re-declaring its own scope.
"""

from __future__ import annotations

import subprocess


def test_ruff_check_clean() -> None:
    """CONVENTIONS.md §9: `uv run ruff check --no-cache .` must exit 0 with no lint violations.

    Cold, not cached: ruff's cache can mask lint errors on freshly created files (observed on
    m0 task-02), so the gate always runs cold.
    """
    proc = subprocess.run(
        ["uv", "run", "ruff", "check", "--no-cache", "."], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"ruff check failed:\n{proc.stdout}\n{proc.stderr}"


def test_ruff_format_clean() -> None:
    """CONVENTIONS.md §9: `uv run ruff format --check .` must exit 0 (already formatted)."""
    proc = subprocess.run(
        ["uv", "run", "ruff", "format", "--check", "."], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"ruff format --check failed:\n{proc.stdout}\n{proc.stderr}"


def test_mypy_clean() -> None:
    """CONVENTIONS.md §9: `uv run mypy --no-incremental` must report no issues (cold cache)."""
    proc = subprocess.run(["uv", "run", "mypy", "--no-incremental"], capture_output=True, text=True)
    assert proc.returncode == 0, f"mypy failed:\n{proc.stdout}\n{proc.stderr}"
