"""Pins `worker.tools.asset_info.AssetInfoTool`: `get_asset_info` answers `{role, exposure,
criticality}` for a sensor hostname from the static `honeypot/assets.yaml` fleet description,
loaded once at construction; a missing or malformed file degrades to `unavailable(...)` and logs
exactly once, never raising into the tool loop (PRD §6.3, m4 task-02).

`.claude/rules/infra.md`: `honeypot/assets.yaml` holds roles and criticality only, never
credentials or internal addresses. `yaml.safe_load` only — never `yaml.load` (arbitrary object
construction); `test_yaml_tags_are_not_constructed` pins that directly against a harmless PoC
tag, never a real attacker payload.

The happy-path and coverage tests exercise the real, tracked `honeypot/assets.yaml` at its real
repo-relative path (the implementer's deliverable) rather than a fixture copy, so a sensor
dropped from the shipped file fails `test_assets_yaml_covers_every_fixture_and_golden_sensor`
directly. Every other test builds its own throwaway YAML under `tmp_path`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import Settings
from core.schemas.alert import SessionAlert
from worker.tools import ToolContext, unavailable
from worker.tools.asset_info import AssetInfoTool

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ASSETS_YAML_PATH = Path("honeypot/assets.yaml")
_DOCKERFILE_PATH = _REPO_ROOT / "infra" / "Dockerfile.api"
_FIXTURES_DIR = _REPO_ROOT / "fixtures" / "alerts"
_GOLDEN_PATH = _REPO_ROOT / "evals" / "golden" / "v1.jsonl"


def _minimal_alert() -> SessionAlert:
    """A minimal, valid `SessionAlert` — `get_asset_info` never reads `ctx.alert` itself."""
    return SessionAlert.model_validate(
        {
            "source": "cowrie",
            "session_id": "s1",
            "src_ip": "203.0.113.10",
            "sensor": "hp-eu-01",
            "events": [
                {
                    "eventid": "cowrie.session.connect",
                    "timestamp": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                    "session": "s1",
                    "src_ip": "203.0.113.10",
                    "sensor": "hp-eu-01",
                }
            ],
        }
    )


def _ctx() -> ToolContext:
    return ToolContext(alert=_minimal_alert(), session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


def _all_used_sensors() -> set[str]:
    """Every sensor hostname referenced by a fixture alert or a golden-set v1 record."""
    sensors: set[str] = set()
    for path in _FIXTURES_DIR.glob("*.json"):
        sensor = json.loads(path.read_text()).get("sensor")
        if isinstance(sensor, str):
            sensors.add(sensor)
    for line in _GOLDEN_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        sensor = json.loads(stripped).get("alert", {}).get("sensor")
        if isinstance(sensor, str):
            sensors.add(sensor)
    return sensors


async def test_known_hostname_returns_role_exposure_criticality() -> None:
    tool = AssetInfoTool.from_path(_ASSETS_YAML_PATH)
    ctx = _ctx()

    result = await tool.run({"hostname": "hp-eu-01"}, ctx)

    assert result == {
        "hostname": "hp-eu-01",
        "role": "ssh-honeypot",
        "exposure": "internet",
        "criticality": "low",
    }


async def test_assets_yaml_covers_every_fixture_and_golden_sensor() -> None:
    tool = AssetInfoTool.from_path(_ASSETS_YAML_PATH)
    ctx = _ctx()

    sensors = _all_used_sensors()
    # M4: guard against a vacuous pass if both source locations ever moved or emptied out.
    assert len(sensors) == 20, (
        f"expected 20 fleet sensors referenced by fixtures+golden, got {sorted(sensors)}"
    )

    missing = []
    for sensor in sorted(sensors):
        result = await tool.run({"hostname": sensor}, ctx)
        if result.get("unavailable"):
            missing.append(sensor)

    assert not missing, f"sensor(s) missing from honeypot/assets.yaml: {missing}"


async def test_unknown_hostname_is_unavailable() -> None:
    tool = AssetInfoTool.from_path(_ASSETS_YAML_PATH)
    ctx = _ctx()

    result = await tool.run({"hostname": "hp-does-not-exist-01"}, ctx)

    assert result == unavailable("unknown_asset")


async def test_missing_or_non_string_hostname_is_invalid_arguments() -> None:
    tool = AssetInfoTool.from_path(_ASSETS_YAML_PATH)
    ctx = _ctx()

    assert await tool.run({}, ctx) == unavailable("invalid_arguments")
    assert await tool.run({"hostname": 1}, ctx) == unavailable("invalid_arguments")


async def test_missing_file_is_unavailable_and_logs_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing_path = tmp_path / "does-not-exist.yaml"
    ctx = _ctx()

    with caplog.at_level(logging.WARNING):
        tool = AssetInfoTool.from_path(missing_path)
        result_1 = await tool.run({"hostname": "hp-eu-01"}, ctx)
        result_2 = await tool.run({"hostname": "hp-eu-01"}, ctx)

    assert result_1 == unavailable("assets_file_missing")
    assert result_2 == unavailable("assets_file_missing")
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


async def test_malformed_yaml_or_wrong_shape_is_unavailable(tmp_path: Path) -> None:
    bad_syntax_path = tmp_path / "bad_syntax.yaml"
    bad_syntax_path.write_text("assets: [1, 2")
    wrong_shape_path = tmp_path / "wrong_shape.yaml"
    wrong_shape_path.write_text("assets: {x: {role: 1}}")
    ctx = _ctx()

    bad_syntax_tool = AssetInfoTool.from_path(bad_syntax_path)
    wrong_shape_tool = AssetInfoTool.from_path(wrong_shape_path)

    assert await bad_syntax_tool.run({"hostname": "hp-eu-01"}, ctx) == unavailable(
        "assets_file_invalid"
    )
    assert await wrong_shape_tool.run({"hostname": "hp-eu-01"}, ctx) == unavailable(
        "assets_file_invalid"
    )


async def test_yaml_tags_are_not_constructed(tmp_path: Path) -> None:
    """M4 task-02 review C1: an `!!python/object/apply:os.system` payload lands on
    `unavailable("assets_file_invalid")` under BOTH the safe and an unsafe loader (`os.system`
    returns an int exit status; `AssetsFile.model_validate` then rejects `{"assets": 0}` with a
    `ValidationError` regardless), so it pinned nothing. This payload instead deserializes into a
    VALID `AssetsFile` under `yaml.load(..., Loader=yaml.UnsafeLoader)` — the tool would then
    answer `hp-eu-01` normally — so only `yaml.safe_load`'s refusal of every `!!python/` tag
    routes this to `assets_file_invalid`.
    """
    path = tmp_path / "danger.yaml"
    path.write_text(
        "assets: !!python/object/apply:dict "
        "[[[hp-eu-01, {role: ssh-honeypot, exposure: internet, criticality: low}]]]\n"
    )
    ctx = _ctx()

    tool = AssetInfoTool.from_path(path)
    result = await tool.run({"hostname": "hp-eu-01"}, ctx)

    assert result == unavailable("assets_file_invalid")

    # m4 fix-wave (review finding task-02 N3): the same mapping, written UNTAGGED, must load and
    # answer the record normally — proving `assets_file_invalid` above is caused by the
    # `!!python/object/apply:` tag itself, not by some accidental shape mismatch against
    # `AssetRecord`. Without this companion, a future `AssetRecord` schema change that made this
    # exact mapping fail validation on ANY loader (safe or unsafe) would keep
    # `test_yaml_tags_are_not_constructed` green for the wrong reason, defanging the tag check.
    untagged_path = tmp_path / "safe.yaml"
    untagged_path.write_text(
        "assets:\n  hp-eu-01: {role: ssh-honeypot, exposure: internet, criticality: low}\n"
    )

    untagged_tool = AssetInfoTool.from_path(untagged_path)
    untagged_result = await untagged_tool.run({"hostname": "hp-eu-01"}, ctx)

    assert untagged_result == {
        "hostname": "hp-eu-01",
        "role": "ssh-honeypot",
        "exposure": "internet",
        "criticality": "low",
    }


async def test_non_utf8_file_is_unavailable_invalid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """I1: a non-UTF-8 `assets.yaml` must degrade like any other malformed file, never raise a
    `UnicodeDecodeError` out of `from_path` (Goal: "a missing or malformed file degrades ... never
    raising"). Only one WARNING across construction plus two runs, same as the missing-file case.
    """
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"assets:\n  hp-eu-01: {role: \xf3\xa0}\n")
    ctx = _ctx()

    with caplog.at_level(logging.WARNING):
        tool = AssetInfoTool.from_path(path)
        result_1 = await tool.run({"hostname": "hp-eu-01"}, ctx)
        result_2 = await tool.run({"hostname": "hp-eu-01"}, ctx)

    assert result_1 == unavailable("assets_file_invalid")
    assert result_2 == unavailable("assets_file_invalid")
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


async def test_extra_record_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "extra_key.yaml"
    path.write_text(
        "assets:\n"
        "  hp-eu-01:\n"
        "    role: ssh-honeypot\n"
        "    exposure: internet\n"
        "    criticality: low\n"
        "    region: eu-west-1\n"
    )
    ctx = _ctx()

    tool = AssetInfoTool.from_path(path)
    result = await tool.run({"hostname": "hp-eu-01"}, ctx)

    assert result == {
        "hostname": "hp-eu-01",
        "role": "ssh-honeypot",
        "exposure": "internet",
        "criticality": "low",
    }


def test_tool_settings_defaults_and_bounds() -> None:
    settings = Settings()

    assert settings.tool_session_commands_max == 40
    assert settings.tool_session_downloads_max == 10
    assert settings.tool_command_max_chars == 200
    assert settings.assets_yaml_path == "honeypot/assets.yaml"

    with pytest.raises(ValidationError):
        Settings(tool_session_commands_max=0)


def test_api_image_copies_assets_yaml() -> None:
    lines = [line.strip() for line in _DOCKERFILE_PATH.read_text().splitlines()]

    assert "COPY honeypot/assets.yaml ./honeypot/assets.yaml" in lines, (
        "builder stage is missing `COPY honeypot/assets.yaml ./honeypot/assets.yaml`"
    )

    from_indices = [i for i, line in enumerate(lines) if line.upper().startswith("FROM ")]
    assert from_indices, "no FROM instruction found in infra/Dockerfile.api"
    runtime_lines = lines[from_indices[-1] :]

    assert any(
        line.startswith("COPY")
        and "--from=builder" in line
        and "/app/honeypot" in line
        and line.endswith("./honeypot")
        for line in runtime_lines
    ), "runtime stage is missing `COPY --from=builder ... /app/honeypot ./honeypot`"
