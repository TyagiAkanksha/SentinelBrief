"""Regenerate the committed OpenAPI baseline (CONVENTIONS.md §8) from a DB-less `create_app()`.

Run after any route/DTO change: `uv run python scripts/export_openapi.py`, then `git diff` the
result — no drift means the wire surface did not move. `--out PATH` writes somewhere other than
the tracked baseline (used by `tests/test_openapi_baseline.py`'s determinism test and CI's drift
check, neither of which should ever touch the tracked file directly).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from api.factory import create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = _REPO_ROOT / "api" / "openapi.json"


def render_openapi() -> str:
    """Render `create_app().openapi()` deterministically: sorted-key JSON, trailing newline."""
    spec: dict[str, Any] = create_app().openapi()
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write `render_openapi()`'s output to `--out` (default `DEFAULT_OUT`).

    Args:
        argv: Command-line arguments, or `None` to read `sys.argv`.

    Returns:
        `0` on success.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_openapi())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
