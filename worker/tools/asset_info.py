"""`AssetInfoTool`: `get_asset_info` (PRD §6.3) — `{role, exposure, criticality}` for a honeypot
sensor hostname, read once at construction from the static `honeypot/assets.yaml` fleet
description (`.claude/rules/infra.md`: roles and criticality only, never credentials or internal
addresses). A missing or malformed file degrades to `unavailable(...)` and logs exactly once at
construction — never raising into the tool loop, never re-logging per call.

Loaded with `yaml.safe_load` only, never `yaml.load` — the fleet file must never be able to
construct an arbitrary Python object (PRD §10.6: this data path never touches an attacker, but
the same discipline applies to every YAML load in this codebase).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from worker.tools.base import ToolContext, unavailable

logger = logging.getLogger(__name__)


class AssetRecord(BaseModel):
    """One `honeypot/assets.yaml` entry. `extra="ignore"`: a future `region:` key (or similar)
    must not break the tool — only `role`/`exposure`/`criticality` are ever returned.
    """

    model_config = ConfigDict(extra="ignore")

    role: str
    exposure: Literal["internet", "internal"]
    criticality: Literal["low", "medium", "high"]


class AssetsFile(BaseModel):
    """The whole `honeypot/assets.yaml` document, keyed by sensor hostname."""

    assets: dict[str, AssetRecord]


class AssetInfoTool:
    """`get_asset_info(hostname) -> {role, exposure, criticality}` (PRD §6.3)."""

    name = "get_asset_info"
    external = False
    description = (
        "Describe the honeypot sensor the attacker hit: its role, exposure and criticality."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "hostname": {
                "type": "string",
                "description": "The sensor hostname from the alert summary.",
            }
        },
        "required": ["hostname"],
        "additionalProperties": False,
    }

    def __init__(self, assets: Mapping[str, AssetRecord], *, load_error: str | None = None) -> None:
        """Hold the already-loaded fleet (or a load failure reason).

        Args:
            assets: The fleet, keyed by hostname. Empty when `load_error` is set.
            load_error: `None` on success; otherwise the stable reason every `run` call answers
                with (`"assets_file_missing"` or `"assets_file_invalid"`).
        """
        self._assets = dict(assets)
        self._load_error = load_error

    @classmethod
    def from_path(cls, path: Path) -> AssetInfoTool:
        """Load `path` once; degrade to an empty, erroring tool on any failure.

        Args:
            path: The `honeypot/assets.yaml`-shaped file to load (`Settings.assets_yaml_path`).

        Returns:
            An `AssetInfoTool` over the loaded fleet, or one whose every `run` call answers
            `unavailable("assets_file_missing")` (path does not exist / can't be read) or
            `unavailable("assets_file_invalid")` (bad YAML syntax, or the wrong shape) — logging
            exactly one WARNING naming the path (never the file's contents) in either case.
        """
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            logger.warning("assets file missing path=%s", path)
            return cls({}, load_error="assets_file_missing")

        try:
            raw = yaml.safe_load(raw_bytes.decode("utf-8"))
            parsed = AssetsFile.model_validate(raw)
        except (UnicodeDecodeError, yaml.YAMLError, ValidationError):
            logger.warning("assets file invalid path=%s", path)
            return cls({}, load_error="assets_file_invalid")

        return cls(parsed.assets)

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Answer `{hostname, role, exposure, criticality}` for `arguments["hostname"]`.

        Args:
            arguments: `{"hostname": str}`.
            ctx: Unused — the fleet is static, loaded once at construction.

        Returns:
            `unavailable("invalid_arguments")` if `hostname` is missing or not a string;
            `unavailable(self._load_error)` if the fleet file failed to load; otherwise
            `unavailable("unknown_asset")` if `hostname` isn't in the fleet; otherwise the
            record's `role`/`exposure`/`criticality`.
        """
        hostname = arguments.get("hostname")
        if not isinstance(hostname, str):
            return unavailable("invalid_arguments")
        if self._load_error is not None:
            return unavailable(self._load_error)
        record = self._assets.get(hostname)
        if record is None:
            return unavailable("unknown_asset")
        return {
            "hostname": hostname,
            "role": record.role,
            "exposure": record.exposure,
            "criticality": record.criticality,
        }
