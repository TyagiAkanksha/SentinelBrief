"""Pins `scripts/check_real_sessions.py::_safe_name` (m7 task-08, M6 final review t06 N6):
markdown emphasis (`**`/`__`) must be collapsed, not passed straight through. Today's allowed
charset (`_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.:*-]")`) admits both `*` and `_`
unconditionally — `*` for the loc-key `*` marker, `_` for ordinary snake_case field names — so a
Cowrie-controlled eventid or field name containing `**bold**` or `__underline__` renders as live
markdown bold/underline in the report table instead of a defanged literal.

Loaded via `importlib.util.spec_from_file_location`, mirroring `tests/test_check_real_sessions_
hygiene.py::_load_check_real_sessions` (test files never import from each other, CONVENTIONS.md
§10) — no DB needed, `_safe_name` is a pure function.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_real_sessions.py"


def _load_check_real_sessions() -> ModuleType:
    """Load `scripts/check_real_sessions.py` as a standalone module — mirrors the pinned file's
    own `_load_check_real_sessions` (test files never import from each other)."""
    spec = importlib.util.spec_from_file_location("check_real_sessions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_markdown_emphasis_collapsed() -> None:
    """M6 final review t06 N6: a run of 2+ `*` or `_` collapses to one, so a name built entirely
    of emphasis markers can no longer open live markdown bold/italic/underline in the rendered
    report table — mutant: leaving `_UNSAFE_NAME_CHARS` as today's allow-everything-in-that-set
    regex, which lets `**`/`__` straight through unchanged.
    """
    module = _load_check_real_sessions()

    assert module._safe_name("**bold**") == "*bold*"
    assert module._safe_name("__underline__") == "_underline_"
    assert module._safe_name("***triple***") == "*triple*"

    # Ordinary names (a single marker, not a run) must survive unchanged — this is a collapse of
    # repeated markers, not a ban on `*`/`_` themselves (the loc-key `*` marker and normal
    # snake_case field names both depend on that).
    assert module._safe_name("plain_name") == "plain_name"
    assert module._safe_name("events.*.timestamp") == "events.*.timestamp"
