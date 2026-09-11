"""Smoke test: the M0 package skeleton (PRD §4) is importable end to end.

Without this, the CI coverage gate (`--cov=api --cov=worker --cov=core --cov=evals
--cov-fail-under=90`, CONVENTIONS.md §9) would report 0% — nothing else in this task's
suite imports these placeholder modules in-process, since the other gates-as-tests only
shell out to subprocesses.

Since m2 task-02, `api.main` fails fast on empty required settings (CONVENTIONS.md §5) —
importing it has side effects, so `tests/test_api_main.py` owns that module exclusively (m2
task-04) and it is deliberately excluded from the module list below. `worker.main` gained the
same fail-fast wiring at m5 task-01 (spine M5-b); `tests/test_worker_main.py` owns it exclusively
the same way, so it is excluded here too.
"""

from __future__ import annotations

import importlib


def test_all_scaffold_packages_import_cleanly() -> None:
    """PRD §4 / CONVENTIONS.md §2: every module named in the import-linter contracts exists."""
    modules = [
        "api",
        "core",
        "core.llm",
        "core.models",
        "core.schemas",
        "core.services",
        "worker",
        "evals",
    ]
    for name in modules:
        assert importlib.import_module(name) is not None
