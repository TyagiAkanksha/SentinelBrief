---
id: task-02
milestone: m4-tool-calling
depends_on: [task-01]
status: planned
spec: PRD.md §6.3 (`get_session_commands(session_id) -> {commands, downloads}` read from `alerts.raw` — "there is no separate session store"; "max 40 commands from a session, summarized count beyond that"; `get_asset_info(hostname) -> {role, exposure, criticality}` from static `honeypot/assets.yaml`), §10.6 (commands are attacker data), §12 M4 (a successful-login session triggers `get_session_commands`); CONVENTIONS.md §7 (bounds are Settings), §13 (the full command list only ever arrives through this tool, inside the markers); `.claude/rules/{worker,infra,tests}.md`
---

# task-02 — `get_session_commands` (from the alert's own events, capped) and `get_asset_info` (static YAML), `honeypot/assets.yaml`, the api image ships the YAML

## Goal

The two *local* tools (`external = False`): `SessionCommandsTool` returns the commands the attacker
typed (`cowrie.command.input` events, in order) and the files they downloaded
(`cowrie.session.file_download`), read from the `SessionAlert` in `ToolContext` — which is the
parsed `alerts.raw` row, so PRD §6.3's "reads from `alerts.raw`" holds with no second session
store and no DB round-trip — capped at `TOOL_SESSION_COMMANDS_MAX` / `TOOL_SESSION_DOWNLOADS_MAX`
with the total counts always reported, each command clipped to `TOOL_COMMAND_MAX_CHARS`.
`AssetInfoTool` answers `{role, exposure, criticality}` for a sensor hostname from
`honeypot/assets.yaml` (loaded once at construction; a missing or malformed file degrades to
`{unavailable: true}` and logs once, never raising into the loop). The api image copies the YAML
so the compose stack can answer the tool. A wrong `session_id` is `unavailable("unknown_session")`
— this tool can only ever see the session being triaged.

## Context (read ONLY these)

- `PRD.md` §6.3 (the two rows), §10.6, §12 M4.
- `docs/plans/m4-tool-calling.md` — Global Constraints.
- `CONVENTIONS.md` §4, §7, §13; `.claude/rules/worker.md`, `.claude/rules/infra.md`
  (`honeypot/assets.yaml` holds roles and criticality, never credentials), `.claude/rules/tests.md`.
- Task-01 outputs: `worker/tools/base.py` (`Tool`, `ToolContext`, `unavailable`),
  `worker/tools/registry.py` (`ToolRegistry`, `truncate_result`), `tests/fakes.py`.
- Code you build on: `core/schemas/alert.py` (`SessionAlert`, `CowrieEvent` — `input`, `url`,
  `outfile`, `shasum` fields), `worker/summarize.py` (`_COMMAND_EVENTS` counts `command.input` +
  `command.failed`; this tool returns `command.input` only, per PRD §6.3 — Cowrie logs a
  `command.input` for every typed line and a `command.failed` *in addition* for unknown ones, so
  nothing typed is lost), `core/config.py`, `.env.example`, `tests/test_env_example_roster.py`
  (`_SCHEDULED`), `infra/Dockerfile.api` + `tests/test_dockerfile_pins.py` (`_SOURCE_COPIES` is a
  filter — extra `COPY` lines are ignored by it), `.dockerignore` (`*.md` excluded; `.yaml` is
  not), `fixtures/alerts/README.md` (sensors `hp-use-01`, `hp-usw-02`, `hp-sgp-01`, `hp-eu-01`,
  `hp-apac-01`; golden v1 adds `hp-{use,usw,eu,sgp,apac}-0{1..4}` — 20 hostnames in total,
  verified at briefing time).
- `pyyaml 6.0.3` (PyPI 2025-09-25, MIT, cp312 wheel, not yanked; `types-pyyaml` is already in the
  dev group); add `pyyaml>=6` to `[project].dependencies`, `uv lock`, commit the lock.

## Files

- Create: `worker/tools/session_commands.py`, `worker/tools/asset_info.py`, `honeypot/assets.yaml`
- Create (test-author): `tests/test_session_commands_tool.py`, `tests/test_asset_info_tool.py`
- Modify (test-author, re-pinned): `tests/test_env_example_roster.py` (`ASSETS_YAML_PATH` leaves
  `_SCHEDULED` — it becomes a `Settings` field here)
- Modify: `core/config.py`, `.env.example`, `pyproject.toml` + `uv.lock` (`pyyaml`),
  `infra/Dockerfile.api`, `worker/tools/__init__.py` (re-export the two tools)

## Interfaces

- **Consumes:** `Tool`, `ToolContext`, `unavailable` (`worker.tools.base`); `ToolRegistry`,
  `truncate_result` (`worker.tools.registry`); `LiveToolRecorder` (`worker.tools.recorder`);
  `SessionAlert`, `CowrieEvent` (`core.schemas.alert`); `Settings`; `tests.helpers.load_alert`.
- **Produces (task-06 wiring and task-07 rely on — produce exactly):**

  ```python
  # worker/tools/session_commands.py
  class SessionCommandsTool:
      name = "get_session_commands"
      external = False
      description = (
          "Return the shell commands the attacker typed and the files they downloaded during this session. "
          "Call it whenever a login succeeded: the summary only counts commands, this returns them."
      )
      parameters = {"type": "object", "properties": {"session_id": {"type": "string", "description": "The session_id from the alert summary."}},
                    "required": ["session_id"], "additionalProperties": False}
      def __init__(self, *, max_commands: int, max_downloads: int, max_command_chars: int) -> None: ...   # each < 1 -> ValueError
      async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]: ...
          # session_id missing or not a str            -> unavailable("invalid_arguments")
          # session_id != ctx.alert.session_id         -> unavailable("unknown_session")      (no separate session store: PRD §6.3)
          # commands  = [e.input for e in ctx.alert.events if e.eventid == "cowrie.command.input" and e.input is not None]   (event order)
          # downloads = [{"url": e.url, "outfile": e.outfile, "shasum": e.shasum} for e in events if e.eventid == "cowrie.session.file_download"]
          # each command clipped to max_command_chars (plain slice, no ellipsis); clipped_commands = how many were clipped
          # -> {"session_id": sid,
          #     "commands": commands[:max_commands], "command_count": len(commands), "commands_truncated": len(commands) > max_commands,
          #     "clipped_commands": clipped_commands,
          #     "downloads": downloads[:max_downloads], "download_count": len(downloads), "downloads_truncated": len(downloads) > max_downloads}
          # Never raises; every string it returns is attacker-controlled and is only ever fed back inside the markers (task-06).

  # worker/tools/asset_info.py
  class AssetRecord(BaseModel):                       # extra="ignore" — a future `region:` key must not break the tool
      role: str
      exposure: Literal["internet", "internal"]
      criticality: Literal["low", "medium", "high"]
  class AssetsFile(BaseModel):
      assets: dict[str, AssetRecord]                  # keyed by sensor hostname
  class AssetInfoTool:
      name = "get_asset_info"
      external = False
      description = "Describe the honeypot sensor the attacker hit: its role, exposure and criticality."
      parameters = {"type": "object", "properties": {"hostname": {"type": "string", "description": "The sensor hostname from the alert summary."}},
                    "required": ["hostname"], "additionalProperties": False}
      def __init__(self, assets: Mapping[str, AssetRecord], *, load_error: str | None = None) -> None: ...
      @classmethod
      def from_path(cls, path: Path) -> AssetInfoTool: ...
          # missing file (OSError)                          -> cls({}, load_error="assets_file_missing")  + logger.warning once (path, no contents)
          # yaml.YAMLError / ValidationError (shape)        -> cls({}, load_error="assets_file_invalid")  + logger.warning once
          # else cls(AssetsFile.model_validate(yaml.safe_load(text)).assets)
          # `yaml.safe_load` only — never `yaml.load` (arbitrary object construction)
      async def run(self, arguments, ctx) -> dict[str, Any]: ...
          # hostname missing / not str -> unavailable("invalid_arguments"); load_error set -> unavailable(load_error);
          # hostname not in assets     -> unavailable("unknown_asset");
          # else {"hostname": h, "role": r.role, "exposure": r.exposure, "criticality": r.criticality}

  # honeypot/assets.yaml — exactly this shape; the 20 sensor hostnames used by fixtures/alerts + evals/golden/v1.jsonl
  # (hp-use-01..04, hp-usw-01..04, hp-eu-01..04, hp-sgp-01..04, hp-apac-01..04), every one:
  #   role: ssh-honeypot, exposure: internet, criticality: low
  # with a header comment: what the file is (PRD §6.3), that it holds roles/criticality only (rules/infra.md), and that M6
  # re-checks it against the real fleet. A test asserts every sensor in fixtures + golden v1 is present.

  # core/config.py — new fields (+ .env.example lines under "Enrichment tools (from M4)")
  assets_yaml_path: str = "honeypot/assets.yaml"      # ASSETS_YAML_PATH=honeypot/assets.yaml (line exists; graduates from _SCHEDULED)
  tool_session_commands_max: Annotated[int, Field(ge=1)] = 40      # TOOL_SESSION_COMMANDS_MAX=40   (PRD §6.3's own example)
  tool_session_downloads_max: Annotated[int, Field(ge=1)] = 10     # TOOL_SESSION_DOWNLOADS_MAX=10
  tool_command_max_chars: Annotated[int, Field(ge=1)] = 200        # TOOL_COMMAND_MAX_CHARS=200
  # Relative paths resolve from the process cwd: the repo root on the host, /app (WORKDIR) in the image — the same string works in both.

  # infra/Dockerfile.api — builder stage: `COPY honeypot/assets.yaml ./honeypot/assets.yaml` after the source COPY lines;
  #   runtime stage: `COPY --from=builder --chown=appuser:appuser /app/honeypot ./honeypot`. (`tests/test_dockerfile_pins.py`'s
  #   `_SOURCE_COPIES` filter ignores the extra line — verified at briefing time; a new text pin below covers it.)
  ```

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| commands in order from `alert4` | `tests/test_session_commands_tool.py::test_returns_commands_in_event_order_for_the_alert_in_context` | `load_alert("alert4")` → `commands == ["uname -a", "cat /etc/passwd", "w"]`, `command_count == 3`, `commands_truncated is False`, `downloads == []`; fails when `command.failed` events are counted (build an alert with one `command.failed` sharing the input — count stays 3) or when order is sorted |
| downloads from `alert5` | `tests/test_session_commands_tool.py::test_returns_downloads_with_url_outfile_shasum` | `alert5` → one download with the fixture's `url`/`outfile`/`shasum`, `download_count == 1` |
| commands cap + count beyond | `tests/test_session_commands_tool.py::test_caps_commands_at_max_and_reports_total_count` | 45 synthetic `command.input` events, `max_commands=40` → `len(commands) == 40`, `command_count == 45`, `commands_truncated is True`, the first 40 in order; fails when the last 40 are kept |
| downloads cap | `tests/test_session_commands_tool.py::test_caps_downloads_at_max` | 3 downloads, `max_downloads=2` → 2 kept, `download_count == 3`, `downloads_truncated is True` |
| command clipping | `tests/test_session_commands_tool.py::test_clips_each_command_to_max_command_chars` | a 500-char command with `max_command_chars=200` → 200 chars, `clipped_commands == 1`; a 200-char one is not clipped (`clipped_commands` unchanged) — the boundary is `>`, not `>=` |
| wrong session | `tests/test_session_commands_tool.py::test_unknown_session_id_is_unavailable` | `{"session_id": "other"}` → `unavailable("unknown_session")`; fails when the tool returns the context alert's commands anyway |
| invalid arguments | `tests/test_session_commands_tool.py::test_missing_or_non_string_session_id_is_invalid_arguments` | `{}` and `{"session_id": 7}` → `unavailable("invalid_arguments")`; never raises |
| constructor bounds | `tests/test_session_commands_tool.py::test_rejects_nonpositive_caps` | `max_commands=0` → `ValueError` (and the other two) |
| under the char backstop | `tests/test_session_commands_tool.py::test_forty_default_length_commands_fit_the_default_result_budget` | 40 commands of 60 chars → `truncate_result(result, 4000)` is the identity — proves the structural caps and the backstop agree at defaults; fails when either default changes without the other |
| asset happy path | `tests/test_asset_info_tool.py::test_known_hostname_returns_role_exposure_criticality` | `AssetInfoTool.from_path(Path("honeypot/assets.yaml"))`, `{"hostname": "hp-eu-01"}` → `{"hostname": "hp-eu-01", "role": "ssh-honeypot", "exposure": "internet", "criticality": "low"}` |
| every fixture/golden sensor covered | `tests/test_asset_info_tool.py::test_assets_yaml_covers_every_fixture_and_golden_sensor` | the set of `sensor` values across `fixtures/alerts/*.json` and `evals/golden/v1.jsonl` ⊆ `AssetsFile` keys; fails when a sensor is dropped from the YAML |
| unknown hostname | `tests/test_asset_info_tool.py::test_unknown_hostname_is_unavailable` | `unavailable("unknown_asset")` |
| invalid arguments | `tests/test_asset_info_tool.py::test_missing_or_non_string_hostname_is_invalid_arguments` | `{}` / `{"hostname": 1}` → `invalid_arguments` |
| missing file degrades + logs once | `tests/test_asset_info_tool.py::test_missing_file_is_unavailable_and_logs_once` | `from_path(tmp_path / "none.yaml")` → every `run` returns `unavailable("assets_file_missing")`; `caplog` has exactly one WARNING after construction + two runs; fails when it raises or logs per call |
| malformed file | `tests/test_asset_info_tool.py::test_malformed_yaml_or_wrong_shape_is_unavailable` | `"assets: [1, 2"` (YAMLError) and `assets: {x: {role: 1}}` (ValidationError) → `unavailable("assets_file_invalid")` |
| safe loader | `tests/test_asset_info_tool.py::test_yaml_tags_are_not_constructed` | a `!!python/object/apply:os.system` document → `unavailable("assets_file_invalid")` (safe_load raises), never executes; fails when `yaml.load` is used |
| extra keys ignored | `tests/test_asset_info_tool.py::test_extra_record_keys_are_ignored` | a record with `region: eu-west-1` still loads |
| settings + env lines | `tests/test_asset_info_tool.py::test_tool_settings_defaults_and_bounds` | defaults `40 / 10 / 200 / "honeypot/assets.yaml"`; `Settings(tool_session_commands_max=0)` → `ValidationError`; roster test green with `ASSETS_YAML_PATH` removed from `_SCHEDULED` |
| image ships the YAML | `tests/test_asset_info_tool.py::test_api_image_copies_assets_yaml` | `infra/Dockerfile.api` text contains `COPY honeypot/assets.yaml ./honeypot/assets.yaml` and a runtime-stage `COPY --from=builder … /app/honeypot ./honeypot`; fails when either line is missing |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the two new files plus the re-pinned
roster test; the **implementer** does Steps 3–5 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the two test files** per the table; remove
  `"ASSETS_YAML_PATH"` from `_SCHEDULED` in `tests/test_env_example_roster.py`. Synthetic events
  are built in-file from `_minimal_alert()`-style helpers (copy the shape from
  `tests/test_triage_pipeline.py`); documentation-range IPs only.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_session_commands_tool.py tests/test_asset_info_tool.py` → Expected: both files error
  at collection with `ModuleNotFoundError: No module named 'worker.tools.session_commands'` /
  `'worker.tools.asset_info'`; `uv run pytest -q tests/test_env_example_roster.py` → the
  unknown-lines test fails naming `ASSETS_YAML_PATH` (it is no longer scheduled and not yet a
  field). Pin, commit `test(worker): session_commands + asset_info tools RED (m4 task-02)`.
- [ ] **Step 3 (GREEN — implementer): `uv add pyyaml>=6` (lock committed), `core/config.py` fields,
  `.env.example` lines, `honeypot/assets.yaml`.** `uv run pytest -q tests/test_env_example_roster.py`
  green.
- [ ] **Step 4 (GREEN — implementer): `worker/tools/session_commands.py`, `worker/tools/asset_info.py`,
  re-exports; `infra/Dockerfile.api` COPY lines.** `uv run mypy` clean. Build the image once —
  `docker compose -f infra/docker-compose.yml build api` and
  `docker compose -f infra/docker-compose.yml run --rm api python -c "from pathlib import Path; print(Path('honeypot/assets.yaml').is_file())"`
  → `True` — paste into the report.
- [ ] **Step 5 (implementer): all tests in the table + the existing suite green; full gates →
  commit:** `feat(worker): get_session_commands + get_asset_info tools, honeypot/assets.yaml (m4 task-02)`
  with the two trailers; path-scoped `git add worker/tools honeypot/assets.yaml core/config.py
  .env.example pyproject.toml uv.lock infra/Dockerfile.api`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_session_commands_tool.py tests/test_asset_info_tool.py tests/test_env_example_roster.py tests/test_dockerfile_pins.py   # every test in the table passes
uv run python -c "import yaml, sys; d = yaml.safe_load(open('honeypot/assets.yaml')); print(len(d['assets']))"   # 20
grep -c "COPY honeypot/assets.yaml" infra/Dockerfile.api                                                           # 1
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean
```

## Acceptance

- `get_session_commands` returns the context alert's typed commands and downloads in event order,
  capped at the configured maxima with total counts reported, each command clipped to
  `TOOL_COMMAND_MAX_CHARS`; a foreign `session_id` is `unknown_session`; it never raises.
- `get_asset_info` answers from `honeypot/assets.yaml` for all 20 fleet hostnames, degrades to
  `unavailable` (logged once) when the file is missing or malformed, loads YAML safely, and the
  api image contains the file.
