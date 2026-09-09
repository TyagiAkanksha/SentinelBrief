"""`SessionCommandsTool`: `get_session_commands` (PRD §6.3) — the commands the attacker typed
(`cowrie.command.input`) and the files they downloaded (`cowrie.session.file_download`), read
straight from the `SessionAlert` already in `ToolContext`. There is no separate session store and
no DB round-trip: the alert being triaged IS the session, so a `session_id` other than the one in
context can only ever be `unknown_session` (PRD §6.3).

Every string this tool returns — commands, download URLs/paths/hashes — is attacker-controlled.
It is only ever fed back to the model inside the `<<<ALERT_DATA>>>` markers with the "data, never
instructions" sentence (PRD §10.6); this module does not do that delimiting itself, task-06's
prompt-building does.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from worker.tools.base import ToolContext, unavailable

_COMMAND_EVENT = "cowrie.command.input"
_DOWNLOAD_EVENT = "cowrie.session.file_download"


class SessionCommandsTool:
    """`get_session_commands(session_id) -> {commands, downloads}` (PRD §6.3)."""

    name = "get_session_commands"
    external = False
    description = (
        "Return the shell commands the attacker typed and the files they downloaded during this "
        "session. Call it whenever a login succeeded: the summary only counts commands, this "
        "returns them."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session_id from the alert summary.",
            }
        },
        "required": ["session_id"],
        "additionalProperties": False,
    }

    def __init__(self, *, max_commands: int, max_downloads: int, max_command_chars: int) -> None:
        """Set the three structural caps, each a `Settings` field (never a literal here).

        Args:
            max_commands: Max commands returned; the rest only count toward `command_count`.
            max_downloads: Max downloads returned; the rest only count toward `download_count`.
            max_command_chars: Max characters each returned command is clipped to.

        Raises:
            ValueError: Any bound is less than 1.
        """
        if max_commands < 1:
            raise ValueError("max_commands must be >= 1")
        if max_downloads < 1:
            raise ValueError("max_downloads must be >= 1")
        if max_command_chars < 1:
            raise ValueError("max_command_chars must be >= 1")
        self._max_commands = max_commands
        self._max_downloads = max_downloads
        self._max_command_chars = max_command_chars

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Return the context alert's commands and downloads; never raises.

        Args:
            arguments: `{"session_id": str}` — must match `ctx.alert.session_id`.
            ctx: The session being triaged; this tool reads `ctx.alert` only.

        Returns:
            `unavailable("invalid_arguments")` if `session_id` is missing or not a string;
            `unavailable("unknown_session")` if it names a different session (no separate session
            store: this tool can only ever see the session being triaged); otherwise the commands
            and downloads found in `ctx.alert.events`, each capped and counted.
        """
        session_id = arguments.get("session_id")
        if not isinstance(session_id, str):
            return unavailable("invalid_arguments")
        if session_id != ctx.alert.session_id:
            return unavailable("unknown_session")

        commands = [
            event.input
            for event in ctx.alert.events
            if event.eventid == _COMMAND_EVENT and event.input is not None
        ]
        downloads = [
            {"url": event.url, "outfile": event.outfile, "shasum": event.shasum}
            for event in ctx.alert.events
            if event.eventid == _DOWNLOAD_EVENT
        ]

        clipped_commands = sum(1 for cmd in commands if len(cmd) > self._max_command_chars)
        clipped = [cmd[: self._max_command_chars] for cmd in commands]

        return {
            "session_id": session_id,
            "commands": clipped[: self._max_commands],
            "command_count": len(commands),
            "commands_truncated": len(commands) > self._max_commands,
            "clipped_commands": clipped_commands,
            "downloads": downloads[: self._max_downloads],
            "download_count": len(downloads),
            "downloads_truncated": len(downloads) > self._max_downloads,
        }
