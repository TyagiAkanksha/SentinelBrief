"""Enrichment-tool seam: `Tool` Protocol, `ToolContext`, `ToolRegistry`, `ToolRecorder` (PRD §6.3,
§7.2, §10.6). See `worker/tools/base.py`, `worker/tools/recorder.py`, `worker/tools/registry.py`
for the "tools never raise" contract and the fixture-replay seam this package builds on.
"""

from __future__ import annotations

from worker.tools.base import Tool, ToolContext, spec_for, unavailable
from worker.tools.recorder import (
    FIXTURE_KEY_CHARS,
    LiveToolRecorder,
    ReplayToolRecorder,
    ToolRecorder,
    fixture_key,
    fixture_path,
    write_fixture,
)
from worker.tools.registry import ToolExecution, ToolRegistry, truncate_result

__all__ = [
    "FIXTURE_KEY_CHARS",
    "LiveToolRecorder",
    "ReplayToolRecorder",
    "Tool",
    "ToolContext",
    "ToolExecution",
    "ToolRecorder",
    "ToolRegistry",
    "fixture_key",
    "fixture_path",
    "spec_for",
    "truncate_result",
    "unavailable",
    "write_fixture",
]
