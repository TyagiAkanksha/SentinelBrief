"""Pins the committed OpenAPI baseline (CONVENTIONS.md §8) — m2 task-02.

`api/openapi.json` is dumped deterministically (`json.dumps(..., indent=2, sort_keys=True)`,
trailing `\\n`) from a DB-less `create_app()` by `scripts/export_openapi.py`. CI diffs the
committed file against a fresh export to catch wire-surface drift; both properties — "the
committed file matches what `create_app()` produces right now" and "the export script is
deterministic" — are pinned here so a route/DTO change without a baseline regeneration fails
loudly instead of silently diverging from `web/`'s codegen.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from api.factory import create_app

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OPENAPI_PATH = _REPO_ROOT / "api" / "openapi.json"
_EXPORT_SCRIPT = _REPO_ROOT / "scripts" / "export_openapi.py"


def test_committed_baseline_matches_app() -> None:
    expected = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"

    assert _OPENAPI_PATH.read_bytes() == expected.encode()


def test_export_script_is_deterministic() -> None:
    subprocess.run([sys.executable, str(_EXPORT_SCRIPT)], cwd=_REPO_ROOT, check=True)
    first = _OPENAPI_PATH.read_bytes()

    subprocess.run([sys.executable, str(_EXPORT_SCRIPT)], cwd=_REPO_ROOT, check=True)
    second = _OPENAPI_PATH.read_bytes()

    assert first == second

    git = shutil.which("git")
    if git is not None:
        result = subprocess.run(
            [git, "diff", "--exit-code", "--", "api/openapi.json"],
            cwd=_REPO_ROOT,
            check=False,
        )
        assert result.returncode == 0, "re-exporting must reproduce the committed baseline exactly"
