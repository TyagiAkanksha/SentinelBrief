"""Pins the v2 loader contract: `GoldenCase.labeled_by`/`labeled_at` and `load_golden(path, *,
require_human=...)` (PRD §7.1, §13; m7 task-01).

`evals.golden.GoldenCase` does not carry `labeled_by`/`labeled_at` yet and `load_golden` does not
accept `require_human` yet, so `test_require_human_rejects_unlabeled_rows` fails on the unknown
keyword argument and `test_v1_loads_unchanged` fails with `AttributeError` on `case.labeled_by` —
never a collection error (m7 task-01 Expected RED signal).

The AST scan below (m7 task-01 Ruling R1) is additional to, and narrower than, the brief's own
`tests/test_label_tool.py::test_only_label_tool_writes_labeled_by_human` (which also greps
`scripts/**/*.py` and checks `evals/sample.py` never imports `GoldenLabel`): this one walks every
`.py` under `evals/` with the `ast` module and asserts the string literal `"human"` is assigned to
a name/keyword/attribute called `labeled_by` in exactly one function, `prompt_label` in
`evals/label_tool.py` — belt and suspenders on PRD §13 ("golden set v2 labels — must be human
work"). Driving `prompt_label` with a fake `Console` returning scripted answers (see
`tests/test_label_tool.py`) is test input, not a golden label; neither this file nor any other
file authored for this task creates or touches `evals/golden/v2.jsonl`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from evals.golden import load_golden

GOLDEN_V1_PATH = Path("evals/golden/v1.jsonl")
EVALS_ROOT = Path("evals")


def _minimal_alert(session_id: str) -> dict[str, object]:
    return {
        "source": "cowrie",
        "session_id": session_id,
        "src_ip": "203.0.113.77",
        "sensor": "hp-test-01",
        "events": [
            {
                "eventid": "cowrie.session.connect",
                "timestamp": "2026-09-06T14:03:21.481902Z",
                "session": session_id,
                "src_ip": "203.0.113.77",
                "sensor": "hp-test-01",
            },
            {
                "eventid": "cowrie.session.closed",
                "timestamp": "2026-09-06T14:03:31.104450Z",
                "session": session_id,
                "src_ip": "203.0.113.77",
                "sensor": "hp-test-01",
                "duration_ms": 9623,
            },
        ],
    }


def _row(*, session_id: str, labeled_by: str | None, labeled_at: str | None) -> dict[str, object]:
    row: dict[str, object] = {
        "alert": _minimal_alert(session_id),
        "label": {"severity": 2, "category": "scanning", "escalate": False},
        "labeler_note": "§6.6 sev 2: synthetic row for the require_human loader contract test.",
        "tags": [],
    }
    if labeled_by is not None:
        row["labeled_by"] = labeled_by
    if labeled_at is not None:
        row["labeled_at"] = labeled_at
    return row


def test_require_human_rejects_unlabeled_rows(tmp_path: Path) -> None:
    rows = [
        _row(session_id="row-1", labeled_by="human", labeled_at="2026-09-20T00:00:00Z"),
        _row(session_id="row-2", labeled_by=None, labeled_at=None),
    ]
    path = tmp_path / "v2-mixed.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    with pytest.raises(ValueError, match="row 2"):
        load_golden(path, require_human=True)

    cases = load_golden(path, require_human=False)
    assert len(cases) == 2
    assert cases[0].labeled_by == "human"
    assert cases[1].labeled_by is None


def test_v1_loads_unchanged() -> None:
    cases = load_golden(GOLDEN_V1_PATH)

    assert len(cases) == 20
    assert all(case.labeled_by is None for case in cases)
    assert all(case.labeled_at is None for case in cases)


# --- Ruling R1: the AST-level "only prompt_label may mint labeled_by='human'" guarantee ----------


def _labeled_by_human_functions(tree: ast.AST) -> list[str]:
    """Every enclosing function name where the string literal `"human"` is assigned (by `=` or as
    a keyword argument) to something named `labeled_by`, anywhere in `tree`."""
    hits: list[str] = []

    def _is_human_literal(node: ast.expr) -> bool:
        return isinstance(node, ast.Constant) and node.value == "human"

    def _target_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._stack: list[str] = []

        def _enter(self, name: str, node: ast.AST) -> None:
            self._stack.append(name)
            self.generic_visit(node)
            self._stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._enter(node.name, node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._enter(node.name, node)

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                if _target_name(target) == "labeled_by" and _is_human_literal(node.value):
                    hits.append(self._stack[-1] if self._stack else "<module>")
            self.generic_visit(node)

        def visit_keyword(self, node: ast.keyword) -> None:
            if node.arg == "labeled_by" and _is_human_literal(node.value):
                hits.append(self._stack[-1] if self._stack else "<module>")
            self.generic_visit(node)

    _Visitor().visit(tree)
    return hits


def test_labeled_by_human_assignment_appears_only_in_prompt_label() -> None:
    occurrences: list[tuple[str, str]] = []
    for path in sorted(EVALS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for func_name in _labeled_by_human_functions(tree):
            occurrences.append((path.as_posix(), func_name))

    assert occurrences == [("evals/label_tool.py", "prompt_label")], (
        "labeled_by='human' must be minted in exactly one place, evals/label_tool.py::"
        f"prompt_label (PRD §13); found: {occurrences}"
    )
