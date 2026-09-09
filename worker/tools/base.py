"""The `Tool` Protocol and `ToolContext` every enrichment tool implements (PRD §6.3).

A `Tool.run` **never raises by contract**: every failure — a bad argument, a missing API key, a
downstream error — is reported as `unavailable(reason)`, never an exception. This is the "tools
never raise" contract (spine constraint M4-a); `worker.tools.registry.ToolRegistry.execute` is
the one deliberate backstop for whatever slips through anyway (controller ruling Q6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import ToolSpec
from core.schemas.alert import SessionAlert


@dataclass(frozen=True)
class ToolContext:
    """The context a tool runs with: the session being triaged, plus deterministic time/DB seams.

    `session` is `None` outside `triage_alert` (the CLI, evals) — a tool that reads the DB
    (`get_alert_history`) must handle that case itself.
    """

    alert: SessionAlert  # the session being triaged — get_session_commands reads it
    session: AsyncSession | None  # the triage transaction's session; None outside triage_alert
    now: datetime  # aware UTC; injected so windows and caches are deterministic in tests


class Tool(Protocol):
    """One enrichment tool the model may call during triage (PRD §6.3)."""

    name: str
    description: str  # what the model reads when deciding to call it
    parameters: dict[str, Any]  # JSON Schema object for `arguments`
    external: bool
    # True: result depends on something outside the alert + repo (network, a downloaded DB, the
    # alerts table) -> ReplayToolRecorder serves it from a fixture; False: deterministic from
    # ctx.alert + repo files -> runs live everywhere.

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Execute the tool and return its (untruncated) result.

        NEVER raises by contract: every failure is `unavailable(reason)` (spine constraint M4-a);
        the tool validates its own arguments. `ToolRegistry.execute`'s backstop catches whatever
        slips through anyway.

        Args:
            arguments: The decoded tool-call arguments (from `ToolCallRequest.arguments`).
            ctx: The session/time/DB context to run against.

        Returns:
            A JSON-serializable result dict — the model sees this (truncated) and it is
            persisted verbatim in the tool-call trace.
        """
        ...


def unavailable(reason: str) -> dict[str, Any]:
    """Build the standard "this tool has nothing to report" result shape.

    Args:
        reason: A short, stable machine-readable reason (e.g. `"unknown_tool"`,
            `"fixture_missing"`).

    Returns:
        `{"unavailable": True, "reason": reason}`.
    """
    return {"unavailable": True, "reason": reason}


def spec_for(tool: Tool) -> ToolSpec:
    """Build the provider-neutral `ToolSpec` the model is offered for `tool`.

    Args:
        tool: The tool to describe.

    Returns:
        `{"name": tool.name, "description": tool.description, "parameters": tool.parameters}`.
    """
    return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
