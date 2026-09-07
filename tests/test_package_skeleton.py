"""Smoke test: the M0 package skeleton (PRD §4) is importable end to end.

Without this, the CI coverage gate (`--cov=api --cov=worker --cov=core --cov=evals
--cov-fail-under=90`, CONVENTIONS.md §9) would report 0% — nothing else in this task's
suite imports these placeholder modules in-process, since the other gates-as-tests only
shell out to subprocesses.

Since m2 task-02, `api.main` fails fast on empty `DATABASE_URL`/`INGEST_HMAC_SECRET`
(CONVENTIONS.md §5) — the two are set here so this smoke test exercises the happy wiring path
instead of the (now expected) `ConfigError`.
"""

from __future__ import annotations

import importlib

import pytest


def test_all_scaffold_packages_import_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD §4 / CONVENTIONS.md §2: every module named in the import-linter contracts exists."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/placeholder")
    monkeypatch.setenv("INGEST_HMAC_SECRET", "test-secret")
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
