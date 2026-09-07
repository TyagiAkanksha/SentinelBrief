"""Regenerate the committed OpenAPI baseline (CONVENTIONS.md §8) from a DB-less `create_app()`.

Run after any route/DTO change: `uv run python scripts/export_openapi.py`, then `git diff` the
result — no drift means the wire surface did not move.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.factory import create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPENAPI_PATH = _REPO_ROOT / "api" / "openapi.json"


def main() -> None:
    """Export `create_app().openapi()` to `api/openapi.json`, deterministically formatted."""
    spec: dict[str, Any] = create_app().openapi()
    _OPENAPI_PATH.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
