"""The `ToolRecorder` seam: live execution (optionally recording) vs. fixture replay (PRD §7.2).

`LiveToolRecorder` always calls `tool.run` and, given `record_dir`, writes a fixture of the
result. `ReplayToolRecorder` serves an *external* tool's result from
`tests/fixtures/tools/<tool>/<key>.json` and never calls its `run` — deterministic evals never hit
a live API (CLAUDE.md, CONVENTIONS.md §10); a local (non-external) tool always runs live under
either recorder since its result is deterministic from `ctx.alert` + repo files. A missing,
mismatched or unreadable fixture is reported via `unavailable(...)`, never raised — the "tools
never raise" contract holds even at the recorder layer, UNLESS `ReplayToolRecorder` was built with
`strict=True` (m7 task-02, PRD §13): a v2 eval run defaults to strict so a case with no fixture
fails the eval loudly (`FixtureMissingError`) instead of silently scoring degraded tool evidence;
a mismatched/unreadable fixture is still never raised even in strict mode — only a genuinely
*missing* file is.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from core.errors import FixtureMissingError
from worker.tools.base import Tool, ToolContext, unavailable

logger = logging.getLogger(__name__)

FIXTURE_KEY_CHARS = 16


def fixture_key(arguments: Mapping[str, Any]) -> str:
    """Derive the canonical, order-independent fixture key for `arguments`.

    Args:
        arguments: The tool-call arguments to key on.

    Returns:
        The first `FIXTURE_KEY_CHARS` hex characters of
        `sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True))`.
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:FIXTURE_KEY_CHARS]


def fixture_path(root: Path, tool_name: str, arguments: Mapping[str, Any]) -> Path:
    """Build the fixture file path for `tool_name` called with `arguments`, under `root`.

    Args:
        root: The fixtures directory root (e.g. `tests/fixtures/tools`).
        tool_name: The tool's `name`.
        arguments: The tool-call arguments the fixture was recorded for.

    Returns:
        `root / tool_name / f"{fixture_key(arguments)}.json"`.
    """
    return root / tool_name / f"{fixture_key(arguments)}.json"


def write_fixture(
    root: Path, tool_name: str, arguments: Mapping[str, Any], result: Mapping[str, Any]
) -> Path:
    """Write a fixture file for `tool_name`/`arguments`/`result` under `root`.

    Args:
        root: The fixtures directory root.
        tool_name: The tool's `name`.
        arguments: The tool-call arguments that produced `result`.
        result: The tool's normalized result to record.

    Returns:
        The path the fixture was written to.
    """
    path = fixture_path(root, tool_name, arguments)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"tool": tool_name, "arguments": dict(arguments), "result": dict(result)}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    return path


class ToolRecorder(Protocol):
    """How `ToolRegistry` runs a tool: live, replayed from a fixture, or (for live) recorded."""

    async def execute(
        self, tool: Tool, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        """Run `tool` with `arguments`/`ctx` and return its (untruncated) result."""
        ...


class LiveToolRecorder:
    """Always executes `tool.run` live; optionally records a fixture of the result."""

    def __init__(self, *, record_dir: Path | None = None) -> None:
        """Build a recorder that always runs live, optionally minting fixtures as it goes.

        Args:
            record_dir: When set, every `execute` call also writes a fixture under this root
                (`LiveToolRecorder(record_dir=Path("tests/fixtures/tools"))` mints fixtures).
        """
        self._record_dir = record_dir

    async def execute(
        self, tool: Tool, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        """Run `tool.run(arguments, ctx)` live, recording a fixture when `record_dir` is set."""
        result = await tool.run(arguments, ctx)
        if self._record_dir is not None:
            write_fixture(self._record_dir, tool.name, arguments, result)
        return result


class ReplayToolRecorder:
    """Serves an external tool's result from a recorded fixture; runs local tools live."""

    def __init__(self, fixtures_dir: Path, *, strict: bool = False) -> None:
        """Build a recorder that replays fixtures from `fixtures_dir`.

        Args:
            fixtures_dir: The fixtures directory root (e.g. `tests/fixtures/tools`).
            strict: When `True`, a missing fixture raises `FixtureMissingError` instead of
                degrading to `unavailable("fixture_missing")` (m7 task-02); default `False`
                preserves the M4-era degrade for every pre-task-02 call site.
        """
        self._fixtures_dir = fixtures_dir
        self._strict = strict
        self._warned: set[Path] = set()  # per-path missing-fixture warning guard (I5)

    async def execute(
        self, tool: Tool, arguments: Mapping[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        """Replay `tool`'s fixture if `tool.external`, else run it live.

        Never calls `tool.run` for an external tool — that is the whole point (PRD §7.2,
        CLAUDE.md "never live APIs"). A mismatched/unreadable/wrong-shaped fixture is always
        reported via `unavailable(...)`, never raised. A *missing* fixture is reported the same
        way UNLESS this recorder is `strict`, in which case it raises `FixtureMissingError(f"
        {tool.name}:{key}")` instead (m7 task-02) — `ToolRegistry.execute`'s one carved-out
        `except FixtureMissingError: raise` lets it propagate rather than being swallowed into
        the registry's own `unavailable(...)` backstop.

        Raises:
            FixtureMissingError: `strict` is `True` and no fixture exists for `tool`/`arguments`.
        """
        if not tool.external:
            return await tool.run(arguments, ctx)

        path = fixture_path(self._fixtures_dir, tool.name, arguments)
        if not path.exists():
            if self._strict:
                raise FixtureMissingError(f"{tool.name}:{path.stem}")
            if path not in self._warned:
                logger.warning("tool fixture missing tool=%s path=%s", tool.name, path)
                self._warned.add(path)
            return unavailable("fixture_missing")

        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return unavailable("fixture_unreadable")

        if (
            not isinstance(data, dict)
            or not {"tool", "arguments", "result"} <= data.keys()
            or not isinstance(data["result"], dict)
        ):
            return unavailable("fixture_unreadable")

        if data["tool"] != tool.name or data["arguments"] != dict(arguments):
            return unavailable("fixture_mismatch")

        return dict(data["result"])
