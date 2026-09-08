"""Pins the committed OpenAPI baseline (CONVENTIONS.md §8) — m2 task-02.

`api/openapi.json` is dumped deterministically (`json.dumps(..., indent=2, sort_keys=True)`,
trailing `\\n`) from a DB-less `create_app()` by `scripts/export_openapi.py`. CI diffs the
committed file against a fresh export to catch wire-surface drift; both properties — "the
committed file matches what `create_app()` produces right now" and "the export script is
deterministic" — are pinned here so a route/DTO change without a baseline regeneration fails
loudly instead of silently diverging from `web/`'s codegen.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_export_script_is_deterministic(tmp_path: Path) -> None:
    """`--out` writes to an explicit path, never the tracked baseline (M2 final review, plan
    defect 5): two independent exports into `tmp_path` are byte-identical to each other and to
    the committed `api/openapi.json` — proving determinism without the test itself ever writing
    the tracked file (unlike the superseded version of this test, which re-exported in place).
    """
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"

    subprocess.run(
        [sys.executable, str(_EXPORT_SCRIPT), "--out", str(out_a)], cwd=_REPO_ROOT, check=True
    )
    subprocess.run(
        [sys.executable, str(_EXPORT_SCRIPT), "--out", str(out_b)], cwd=_REPO_ROOT, check=True
    )

    first = out_a.read_bytes()
    second = out_b.read_bytes()

    assert first == second
    assert first == _OPENAPI_PATH.read_bytes()


def test_export_script_default_out_is_the_tracked_baseline() -> None:
    """`DEFAULT_OUT` (used when `--out` is omitted) is exactly `api/openapi.json` — loaded via
    `importlib.util.spec_from_file_location`, the `scripts/`-has-no-`__init__.py` import pattern
    `tests/test_post_alert.py` already uses, so this never re-runs the script against the tracked
    file (the module's own `if __name__ == "__main__":` guard never fires under this loader).
    """
    spec = importlib.util.spec_from_file_location("export_openapi", _EXPORT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.DEFAULT_OUT == _OPENAPI_PATH
