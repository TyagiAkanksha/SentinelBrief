"""`build_registry`: assembles the five PRD §6.3 enrichment tools from `Settings` (m4 task-06).

The one place that wires every tool's `Settings` fields together, in the PRD §6.3 table order
(`TOOL_NAMES`). `cache` and `http` are the two external seams `api.main`/M5 and the test suite
override: a missing `cache` builds an in-process `InMemoryTTLCache` sized from
`abuseipdb_cache_max_entries`; a missing `http` builds an `httpx.AsyncClient` timed from
`abuseipdb_timeout_s`.

Ownership (m4 task-06 fix-1, I4; owner named at m5 task-01): the default `httpx.AsyncClient` this
function builds — and the GeoIP `.mmdb` readers `GeoAsnTool.from_settings` opens — are
**process-lifetime** objects owned by whoever calls `build_registry`. `worker/main.py::startup`
builds the one process-lifetime `httpx.AsyncClient` and passes it in via `http=`;
`worker/main.py::shutdown` is the owner that calls `aclose()` on it (idempotently, via
`ctx.pop`) when the ARQ worker process shuts down. Every caller must build (and keep) at most one
registry per process — never build a fresh one per unit of work (`scripts/seed_dev.py::seed`
builds exactly one and reuses it across every seeded alert, not one per alert).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from core.cache import InMemoryTTLCache, TTLCache
from core.config import Settings
from worker.tools.alert_history import AlertHistoryTool
from worker.tools.asset_info import AssetInfoTool
from worker.tools.base import Tool
from worker.tools.geo_asn import GeoAsnTool
from worker.tools.ip_reputation import IpReputationTool
from worker.tools.recorder import ToolRecorder
from worker.tools.registry import ToolRegistry
from worker.tools.session_commands import SessionCommandsTool

TOOL_NAMES: tuple[str, ...] = (
    "lookup_ip_reputation",
    "get_ip_geo_asn",
    "get_alert_history",
    "get_session_commands",
    "get_asset_info",
)


def build_registry(
    settings: Settings,
    *,
    recorder: ToolRecorder,
    cache: TTLCache | None = None,
    http: httpx.AsyncClient | None = None,
) -> ToolRegistry:
    """Build the five-tool `ToolRegistry`, in PRD §6.3 table order (`TOOL_NAMES`).

    Args:
        settings: The config surface every tool's bounds/keys/paths are read from.
        recorder: How tools are actually executed (live, replayed from fixtures).
        cache: The `TTLCache` `lookup_ip_reputation` caches successful answers in; `None` builds
            an `InMemoryTTLCache` sized from `abuseipdb_cache_max_entries`.
        http: The `httpx.AsyncClient` `lookup_ip_reputation` issues its request through; `None`
            builds one timed from `abuseipdb_timeout_s`.

    Returns:
        A `ToolRegistry` over all five tools, truncating every result to
        `settings.tool_result_max_chars`.
    """
    tools: list[Tool] = [
        IpReputationTool(
            api_key=settings.abuseipdb_api_key.get_secret_value(),
            http=http or httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s),
            cache=cache or InMemoryTTLCache(max_entries=settings.abuseipdb_cache_max_entries),
            cache_ttl_s=settings.abuseipdb_cache_ttl_s,
            max_age_days=settings.abuseipdb_max_age_days,
            quota_backoff_s=settings.abuseipdb_quota_backoff_s,
        ),
        GeoAsnTool.from_settings(settings),
        AlertHistoryTool(max_window_hours=settings.alert_history_max_window_hours),
        SessionCommandsTool(
            max_commands=settings.tool_session_commands_max,
            max_downloads=settings.tool_session_downloads_max,
            max_command_chars=settings.tool_command_max_chars,
        ),
        AssetInfoTool.from_path(Path(settings.assets_yaml_path)),
    ]
    return ToolRegistry(tools, recorder=recorder, max_result_chars=settings.tool_result_max_chars)
