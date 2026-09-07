"""Smoke test: the M0 package skeleton (PRD §4) is importable end to end.

Without this, the CI coverage gate (`--cov=api --cov=worker --cov=core --cov=evals
--cov-fail-under=90`, CONVENTIONS.md §9) would report 0% — nothing else in this task's
suite imports these placeholder modules in-process, since the other gates-as-tests only
shell out to subprocesses.
"""

from __future__ import annotations

import importlib


def test_all_scaffold_packages_import_cleanly() -> None:
    """PRD §4 / CONVENTIONS.md §2: every module named in the import-linter contracts exists."""
    modules = [
        "api",
        "api.main",
        "core",
        "core.llm",
        "core.models",
        "core.schemas",
        "core.services",
        "worker",
        "worker.main",
        "evals",
    ]
    for name in modules:
        assert importlib.import_module(name) is not None
