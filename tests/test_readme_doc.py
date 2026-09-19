"""Light content pins for `README.md` (m8b task-06): it links `docs/results.md` and
`docs/deployment.md`, and lists the canonical gate commands (CONVENTIONS.md §9,
`docs/FRONTEND-CONVENTIONS.md` §1) verbatim, so a stranger copying commands out of the README runs
the same gate CI does. Deliberately light — this is not a full README content pin.
"""

from __future__ import annotations

from pathlib import Path

README_TEXT = Path("README.md").read_text()

# CONVENTIONS.md §9's five Python gate commands, in order.
_PYTHON_GATE_COMMANDS = [
    "uv run ruff check --no-cache .",
    "uv run ruff format --check .",
    "uv run mypy --no-incremental",
    "uv run lint-imports",
    "uv run pytest -q --cov=api --cov=worker --cov=core --cov=evals "
    "--cov=sentinelbrief_shipper --cov-fail-under=90",
]

# docs/FRONTEND-CONVENTIONS.md §1's four web gate commands.
_WEB_GATE_COMMANDS = [
    "pnpm -C web lint",
    "pnpm -C web type-check",
    "pnpm -C web format:check",
    "pnpm -C web test",
]


def test_readme_links_results_and_deployment_docs() -> None:
    assert "docs/results.md" in README_TEXT
    assert "docs/deployment.md" in README_TEXT


def test_readme_lists_the_canonical_gate_commands() -> None:
    for command in [*_PYTHON_GATE_COMMANDS, *_WEB_GATE_COMMANDS]:
        assert command in README_TEXT, f"README is missing the canonical gate command: {command}"
