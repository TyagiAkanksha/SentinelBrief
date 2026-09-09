"""Enrichment-tool seam: `Tool` Protocol, `ToolContext`, `ToolRegistry`, `ToolRecorder` (PRD §6.3,
§7.2, §10.6). See `worker/tools/base.py`, `worker/tools/recorder.py`, `worker/tools/registry.py`
for the "tools never raise" contract and the fixture-replay seam this package builds on.
"""

from __future__ import annotations

from worker.tools.alert_history import AlertHistoryTool
from worker.tools.asset_info import AssetInfoTool, AssetRecord, AssetsFile
from worker.tools.base import Tool, ToolContext, spec_for, unavailable
from worker.tools.geo_asn import GeoAsnTool, GeoReader, open_reader
from worker.tools.ip_reputation import ABUSEIPDB_CHECK_URL, CACHE_KEY_PREFIX, IpReputationTool
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
from worker.tools.session_commands import SessionCommandsTool
from worker.tools.wiring import TOOL_NAMES, build_registry

__all__ = [
    "ABUSEIPDB_CHECK_URL",
    "CACHE_KEY_PREFIX",
    "FIXTURE_KEY_CHARS",
    "TOOL_NAMES",
    "AlertHistoryTool",
    "AssetInfoTool",
    "AssetRecord",
    "AssetsFile",
    "GeoAsnTool",
    "GeoReader",
    "IpReputationTool",
    "LiveToolRecorder",
    "ReplayToolRecorder",
    "SessionCommandsTool",
    "Tool",
    "ToolContext",
    "ToolExecution",
    "ToolRecorder",
    "ToolRegistry",
    "build_registry",
    "fixture_key",
    "fixture_path",
    "open_reader",
    "spec_for",
    "truncate_result",
    "unavailable",
    "write_fixture",
]
