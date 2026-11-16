"""Only `worker/llm_factory.py` may call `<Client>.from_settings(` (anthropic-haiku task 3).

AST-based, so comments, docstrings and string literals that merely mention the call never count.
Also pins that each production call site routes through `build_llm_client`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("worker", "api", "evals", "scripts", "core")
_CLIENTS = {"OpenAICompatibleLLMClient", "AnthropicLLMClient"}
_FACTORY = "worker/llm_factory.py"
_CALL_SITES = (
    "worker/main.py",
    "worker/triage_one.py",
    "scripts/seed_dev.py",
    "evals/ai_label.py",
    "evals/run.py",
)


def _python_files() -> list[Path]:
    return [
        p
        for d in _SCAN_DIRS
        for p in sorted((_ROOT / d).rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


def _calls(path: Path, names: set[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "from_settings"
            and isinstance(func.value, ast.Name)
            and func.value.id in names
        ):
            found.append((node.lineno, f"{func.value.id}.from_settings("))
        elif isinstance(func, ast.Name) and func.id in names:
            found.append((node.lineno, f"{func.id}("))
    return found


def test_only_factory_calls_client_from_settings() -> None:
    offenders = [
        f"{p.relative_to(_ROOT)}:{line} {what}"
        for p in _python_files()
        if p.relative_to(_ROOT).as_posix() != _FACTORY
        for line, what in _calls(p, _CLIENTS)
        if what.endswith(".from_settings(")
    ]

    assert offenders == []


def test_factory_module_exists_and_is_the_caller() -> None:
    factory = _ROOT / _FACTORY
    assert factory.is_file()
    assert {w for _, w in _calls(factory, _CLIENTS)} >= {
        "OpenAICompatibleLLMClient.from_settings(",
        "AnthropicLLMClient.from_settings(",
    }


@pytest.mark.parametrize("rel", _CALL_SITES)
def test_call_site_uses_build_llm_client(rel: str) -> None:
    tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
    names = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }

    assert "build_llm_client" in names
