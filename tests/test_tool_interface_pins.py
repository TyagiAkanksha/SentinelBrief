"""Pins the model-facing surface of the two m4 task-02 tools — `name`, `external`, and the
`spec_for()` snapshot (`description` + `parameters`) — none of which had a test before this fix
round (m4 task-02 review I2/M1).

`name` is the registry key and the exact string the model emits in a tool call
(`worker/tools/registry.py::ToolRegistry.execute`); `external` gates whether a tool is ever run
live vs. served from a fixture (`worker/tools/recorder.py`) — a local tool silently flipped to
`external = True` would start answering `fixture_missing` instead of running. `description` and
`parameters` are what the model reads to decide whether/how to call the tool at all.

Snapshot values are copied verbatim from the task-02 brief's Interfaces block, not read back from
the implementation — any drift, in either direction, fails.
"""

from __future__ import annotations

from worker.tools import spec_for
from worker.tools.asset_info import AssetInfoTool
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
