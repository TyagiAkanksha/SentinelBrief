"""Pins the model-facing surface of the m4 task-02/task-03 tools — `name`, `external`, and the
`spec_for()` snapshot (`description` + `parameters`) — none of which had a test before the task-02
fix round (m4 task-02 review I2/M1). Task-03 (controller ruling R7) adds `GeoAsnTool`'s case in
the same file, for the same reason.

`name` is the registry key and the exact string the model emits in a tool call
(`worker/tools/registry.py::ToolRegistry.execute`); `external` gates whether a tool is ever run
live vs. served from a fixture (`worker/tools/recorder.py`) — a local tool silently flipped to
`external = True` would start answering `fixture_missing` instead of running (and, for
`GeoAsnTool`, a tool that IS external silently flipped to `False` would start running live). Its
`description` and `parameters` are what the model reads to decide whether/how to call the tool at
all.

Snapshot values are copied verbatim from each task's brief Interfaces block, not read back from
the implementation — any drift, in either direction, fails.

Task-04 (controller ruling R7) adds `IpReputationTool`'s case in the same file, for the same
reason: `name` is what the model must emit to call `lookup_ip_reputation`, and `external = True`
gates whether the tool is ever run live vs. served from a fixture — a silent flip to `False` would
start spending real AbuseIPDB quota inside `ReplayToolRecorder`'s "local tools run live" branch.

Task-05 (controller ruling R7) adds `AlertHistoryTool`'s case in the same file, for the same
reason: `name` is what the model must emit to call `get_alert_history`, and `external = True`
gates whether it is replayed from a fixture — a silent flip to `False` would run the only
DB-touching tool live inside `ReplayToolRecorder`'s "local tools run live" branch, defeating the
whole point of deterministic evals.
"""

from __future__ import annotations

import httpx
import pytest

from core.cache import InMemoryTTLCache
from worker.tools import spec_for
from worker.tools.alert_history import AlertHistoryTool
from worker.tools.asset_info import AssetInfoTool
from worker.tools.geo_asn import GeoAsnTool
from worker.tools.ip_reputation import IpReputationTool
from worker.tools.session_commands import SessionCommandsTool

_SESSION_COMMANDS_SPEC = {
    "name": "get_session_commands",
    "description": (
        "Return the shell commands the attacker typed and the files they downloaded during this "
        "session. Call it whenever a login succeeded: the summary only counts commands, this "
        "returns them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session_id from the alert summary.",
            }
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
}

_ASSET_INFO_SPEC = {
    "name": "get_asset_info",
    "description": (
        "Describe the honeypot sensor the attacker hit: its role, exposure and criticality."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hostname": {
                "type": "string",
                "description": "The sensor hostname from the alert summary.",
            }
        },
        "required": ["hostname"],
        "additionalProperties": False,
    },
}


def test_session_commands_tool_interface_is_pinned() -> None:
    tool = SessionCommandsTool(max_commands=40, max_downloads=10, max_command_chars=200)

    assert tool.name == "get_session_commands"
    assert tool.external is False
    assert spec_for(tool) == _SESSION_COMMANDS_SPEC


def test_asset_info_tool_interface_is_pinned() -> None:
    tool = AssetInfoTool({})

    assert tool.name == "get_asset_info"
    assert tool.external is False
    assert spec_for(tool) == _ASSET_INFO_SPEC


_GEO_ASN_SPEC = {
    "name": "get_ip_geo_asn",
    "description": (
        "Look up the country, autonomous system number and organisation of an IP address from a "
        "local GeoLite2 database (no network)."
    ),
    "parameters": {
        "type": "object",
        "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."}},
        "required": ["ip"],
        "additionalProperties": False,
    },
}


def test_geo_asn_tool_interface_is_pinned() -> None:
    tool = GeoAsnTool(country=None, asn=None)

    assert tool.name == "get_ip_geo_asn"
    assert tool.external is True
    assert spec_for(tool) == _GEO_ASN_SPEC


_IP_REPUTATION_SPEC = {
    "name": "lookup_ip_reputation",
    "description": (
        "Look up an IP address's abuse reputation (AbuseIPDB confidence score 0-100, report "
        "count, last report time). Costs quota; call it once per address."
    ),
    "parameters": {
        "type": "object",
        "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."}},
        "required": ["ip"],
        "additionalProperties": False,
    },
}


def test_ip_reputation_tool_interface_is_pinned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request: {request.method} {request.url}")

    tool = IpReputationTool(
        api_key="",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=InMemoryTTLCache(),
        cache_ttl_s=86400,
        max_age_days=90,
    )

    assert tool.name == "lookup_ip_reputation"
    assert tool.external is True
    assert spec_for(tool) == _IP_REPUTATION_SPEC


_ALERT_HISTORY_SPEC = {
    "name": "get_alert_history",
    "description": (
        "Count earlier sessions from the same source IP in the last N hours, when the first was "
        "seen, and how they were categorised. Use it to tell a first-time visitor from a "
        "persistent attacker."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ip": {"type": "string", "description": "An IPv4 or IPv6 address."},
            "window_hours": {
                "type": "integer",
                "minimum": 1,
                "description": "Look-back window in hours; clamped to the configured maximum.",
            },
        },
        "required": ["ip", "window_hours"],
        "additionalProperties": False,
    },
}


def test_alert_history_tool_interface_is_pinned() -> None:
    tool = AlertHistoryTool(max_window_hours=720)

    assert tool.name == "get_alert_history"
    assert tool.external is True
    assert spec_for(tool) == _ALERT_HISTORY_SPEC
