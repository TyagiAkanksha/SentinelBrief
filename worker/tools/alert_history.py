"""`get_alert_history`: the only tool that touches the database (PRD §6.3, §6.2).

`AlertHistoryTool` validates and clamps its arguments, excludes the session being triaged by
fingerprint, and runs `core.services.alert_history.get_alert_history` inside a
`session.begin_nested()` SAVEPOINT so a database error can never poison the triage transaction
`persist_verdict` needs afterwards (PRD §6.2) — the SAVEPOINT rolls back on failure and the outer
transaction stays usable. Without a session (the CLI, evals) it answers `unavailable("no_database")`
before ever touching SQL.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from core.services.alert_history import get_alert_history
from worker.tools.base import ToolContext, unavailable


class AlertHistoryTool:
    """`get_alert_history(ip, window_hours) -> {ip, window_hours, count, first_seen, categories}`
    (PRD §6.3)."""

    name = "get_alert_history"
    external = True  # depends on the alerts table -> replayed from fixtures in evals
    description = (
        "Count earlier sessions from the same source IP in the last N hours, when the first was "
        "seen, and how they were categorised. Use it to tell a first-time visitor from a "
        "persistent attacker."
    )
    parameters = {
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
    }

    def __init__(self, *, max_window_hours: int) -> None:
        """Build the tool bounded by `max_window_hours` (`ALERT_HISTORY_MAX_WINDOW_HOURS`).

        Args:
            max_window_hours: The largest `window_hours` a caller may request; must be >= 1.

        Raises:
            ValueError: When `max_window_hours` is not positive.
        """
        if max_window_hours < 1:
            raise ValueError("max_window_hours must be >= 1")
        self._max_window_hours = max_window_hours

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Answer `{ip, window_hours, count, first_seen, categories}`; never raises."""
        ip = arguments.get("ip")
        if not isinstance(ip, str):
            return unavailable("invalid_arguments")
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            return unavailable("invalid_arguments")

        window_hours = arguments.get("window_hours")
        if not isinstance(window_hours, int) or isinstance(window_hours, bool) or window_hours < 1:
            return unavailable("invalid_arguments")

        if ctx.session is None:
            return unavailable("no_database")

        window_hours = min(window_hours, self._max_window_hours)
        since = ctx.now - timedelta(hours=window_hours)

        try:
            async with ctx.session.begin_nested():
                history = await get_alert_history(
                    ctx.session,
                    src_ip=ip,
                    since=since,
                    exclude_fingerprint=ctx.alert.fingerprint(),
                )
        except SQLAlchemyError:
            return unavailable("database_error")

        return {
            "ip": ip,
            "window_hours": window_hours,
            "count": history.count,
            "first_seen": (
                history.first_seen.isoformat() if history.first_seen is not None else None
            ),
            "categories": history.categories,
        }
