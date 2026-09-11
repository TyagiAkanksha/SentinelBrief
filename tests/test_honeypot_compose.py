"""Pins `honeypot/docker-compose.yml`, `honeypot/etc/cowrie.cfg`, `honeypot/README.md`, and
`honeypot/assets.yaml`'s header comment against the m6 task-01 brief's Interfaces → test table
(`docs/plans/m6-real-data-deploy/task-01-cowrie-host.md` lines 137-157) and Produces block (lines
71-135): the honeypot host runs exactly one thing (Cowrie), publishes the host's real port 22 to
the container's default 2222 on every interface (not loopback — the one compose file in this repo
that must be reachable from the public internet), bind-mounts its JSON log and artifact
directories under `honeypot/data/` (never a named volume, so task-02's unprivileged `shipper`
systemd unit can read them without running as root), bind-mounts `cowrie.cfg` read-only, carries
no secret/`env_file`/second service, restarts unattended, rotates its logs the same way every
other production service does (`.claude/rules/infra.md`), and pins its image to either the moving
`:latest` tag (until the runbook's digest-pin step) or a `@sha256:<64 hex>` digest. The cfg pins
the JSON output plugin and the deployed sensor name (`hp-use-01`, PRD §1.2: one alert = one
Cowrie session). The assets test pins that the header names `hp-use-01` as the deployed sensor
without disturbing the 20-host fleet shape `tests/test_asset_info_tool.py` already pins. The
runbook test pins the hardening steps (SSM-core-only role, `sshd` masked, 443-only egress,
digest pin) and that no real `INGEST_HMAC_SECRET` value is ever written into the repo
(`.claude/rules/infra.md`: no secret value anywhere in the repo).

The compose-render helper (`_render_compose_config`) is copied — not imported — from
`tests/test_compose_config.py::_render_compose_config` (tests/ has no `__init__.py`;
CONVENTIONS.md §10 requires unique basenames across the tree, so cross-test-file imports are not
used). It skips by name (`pytest.skip("docker not on PATH")`) when `docker` is absent, so every
compose-rendering test in this module skips together rather than erroring; the cfg, assets, and
README tests need no `docker` and always run. Per `.claude/rules/infra.md` ("never paste `docker
compose config` output into a report, ledger, or transcript"), no test in this module ever prints
the rendered config; `_render_compose_config`'s own failure message intentionally omits stdout —
unlike `test_compose_config.py`'s helper — because on RED the only possible failure is the
`honeypot/docker-compose.yml` existence assert below it, never a `docker compose` invocation.

`honeypot/etc/cowrie.cfg` is parsed with `configparser.ConfigParser(interpolation=None)`: Cowrie's
own `${honeypot:log_path}` cross-section reference is Cowrie's interpolation syntax, not
configparser's, and would raise `InterpolationMissingOptionError` under the (default) basic
interpolation.

`honeypot/assets.yaml` is read with `yaml.safe_load` — never `yaml.load` (`.claude/rules/
infra.md`) — the same way `worker.tools.asset_info.AssetInfoTool` does (see
`tests/test_asset_info_tool.py`'s docstring). `pyyaml` is confirmed importable in this
environment (`uv run python -c "import yaml"` succeeds; it is a runtime dependency of
`worker.tools.asset_info`), so a line parser is not needed here.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HONEYPOT_DIR = _REPO_ROOT / "honeypot"
_COMPOSE_FILE = _HONEYPOT_DIR / "docker-compose.yml"
_CFG_FILE = _HONEYPOT_DIR / "etc" / "cowrie.cfg"
_README_FILE = _HONEYPOT_DIR / "README.md"
_ASSETS_YAML_FILE = _HONEYPOT_DIR / "assets.yaml"

_ENV_FILE_LEAK_CANARY = "SENTINELBRIEF_TEST_CANARY=canary\n"
"""Synthetic, non-secret `.env` content for the no-secrets row only (brief Interfaces → test
table, 'no secrets' row) — copied from `tests/test_compose_config.py::_ENV_FILE_LEAK_CANARY`'s
pattern. `docker compose config` folds a service's `env_file:` variables into its rendered
`environment` dict but never renders the `env_file` key itself, so against an empty `.env` a
leaked `env_file: ../.env` would add zero observable keys and go undetected by an
`environment`-shape assertion alone; the canary makes a leaked `env_file` observable without ever
exercising a real secret.
"""

_IMAGE_PIN_RE = re.compile(r"^cowrie/cowrie@sha256:[0-9a-f]{64}$")


def _render_compose_config(tmp_path: Path, *, env_text: str = "") -> dict[str, Any]:
    """Renders `honeypot/docker-compose.yml` with `docker compose config --format json` from a
    throwaway `<tmp_path>/honeypot/docker-compose.yml` copy and returns the parsed config. Skips
    (by name, not a silent pass) when `docker` is not on PATH.

    Copies `honeypot/etc/cowrie.cfg` beside the compose copy at `<tmp_path>/honeypot/etc/
    cowrie.cfg` when it already exists (it does not yet on RED — task-01's GREEN step creates it)
    so the compose file's relative bind-mount source `./etc/cowrie.cfg` resolves under the same
    tree it will on the real host; `docker compose config` does not require a bind-mount source
    to exist on disk to render successfully, so the cfg file's absence here never by itself
    explains a render failure.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH")

    assert _COMPOSE_FILE.exists(), (
        f"{_COMPOSE_FILE} does not exist yet — task-01's GREEN step creates it per the brief's "
        "Interfaces block."
    )

    tmp_honeypot = tmp_path / "honeypot"
    tmp_honeypot.mkdir()
    tmp_compose = tmp_honeypot / "docker-compose.yml"
    shutil.copy(_COMPOSE_FILE, tmp_compose)

    if _CFG_FILE.exists():
        tmp_etc = tmp_honeypot / "etc"
        tmp_etc.mkdir()
        shutil.copy(_CFG_FILE, tmp_etc / "cowrie.cfg")

    (tmp_path / ".env").write_text(env_text)

    proc = subprocess.run(
        ["docker", "compose", "-f", str(tmp_compose), "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "sentinelbrief-honeypot-test"},
    )
    assert proc.returncode == 0, (
        "docker compose config failed (stdout/stderr withheld per .claude/rules/infra.md — "
        f"never paste rendered compose config); returncode={proc.returncode}"
    )
    config: dict[str, Any] = json.loads(proc.stdout)
    return config


def _parse_cowrie_cfg() -> configparser.ConfigParser:
    """Parses `honeypot/etc/cowrie.cfg` with `interpolation=None` (module docstring). Raises via
    the explicit existence assert below (not a silent `configparser.read` no-op on a missing
    path) so the RED failure names the missing file, not a mysterious `NoSectionError`.
    """
    assert _CFG_FILE.exists(), (
        f"{_CFG_FILE} does not exist yet — task-01's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(_CFG_FILE)
    return parser


def _read_assets_yaml() -> tuple[str, dict[str, Any]]:
    """Returns `(header_text, assets)` for `honeypot/assets.yaml`: `header_text` is every line
    before the top-level `assets:` key (the file's leading comment block), and `assets` is
    `yaml.safe_load(...)["assets"]`.
    """
    assert _ASSETS_YAML_FILE.exists(), f"{_ASSETS_YAML_FILE} does not exist"
    text = _ASSETS_YAML_FILE.read_text()
    header_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("assets:"):
            break
        header_lines.append(line)
    header = "\n".join(header_lines)
    data = yaml.safe_load(text)
    assets: dict[str, Any] = data["assets"]
    return header, assets


def test_honeypot_compose_validates_and_has_only_cowrie(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'validates' row (task-01-cowrie-host.md line 148): `docker
    compose config` exits 0 and the rendered service set is exactly `{"cowrie"}` — a stray second
    service (the mutant this test kills) would defeat 'the honeypot host runs exactly one thing'
    (brief Goal, line 13) and the Produces block's explicit 'NO ... other services' comment
    (line 86).
    """
    config = _render_compose_config(tmp_path)
    assert set(config["services"]) == {"cowrie"}, config["services"]


def test_cowrie_publishes_host_port_22_to_2222(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'port 22 → 2222' row (line 149; Produces block lines
    78-79): `cowrie` publishes exactly one port, the host's real 22 mapped to the container's
    Cowrie-default 2222, and — unlike every other compose file in this repo (`.claude/rules/
    infra.md`: dev compose publishes on 127.0.0.1 only) — is NOT bound to `127.0.0.1`: attackers
    on the public internet must be able to reach it, so a copy-pasted loopback-only mapping (the
    mutant this test kills) would silently take the honeypot offline for its entire purpose.
    """
    config = _render_compose_config(tmp_path)
    ports = config["services"]["cowrie"].get("ports", [])
    assert len(ports) == 1, ports
    port = ports[0]
    assert str(port["published"]) == "22", port
    assert port["target"] == 2222, port
    assert port.get("host_ip") != "127.0.0.1", port


def test_cowrie_log_and_lib_are_bind_mounts_under_data(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'log + lib binds' row (line 150; Produces block lines 81-82,
    86-89): the JSON log dir and the tty/download artifact dir are host bind mounts under
    `honeypot/data/` — not a named volume, which task-02's unprivileged systemd `shipper` unit
    (never root) could not read under `/var/lib/docker` — and neither is read-only (Cowrie must
    write them). `config.get("volumes") in (None, {})` pins that no top-level `volumes:` key
    exists at all, per the Produces block's explicit 'NO ... top-level volumes' comment.
    """
    config = _render_compose_config(tmp_path)
    cowrie = config["services"]["cowrie"]
    mounts = {
        mount["target"]: mount for mount in cowrie.get("volumes", []) if mount.get("type") == "bind"
    }

    log_mount = mounts.get("/cowrie/cowrie-git/var/log/cowrie")
    assert log_mount is not None, cowrie.get("volumes")
    assert (
        Path(log_mount["source"]).resolve() == (tmp_path / "honeypot" / "data" / "log").resolve()
    ), log_mount
    assert not log_mount.get("read_only"), log_mount

    lib_mount = mounts.get("/cowrie/cowrie-git/var/lib/cowrie")
    assert lib_mount is not None, cowrie.get("volumes")
    assert (
        Path(lib_mount["source"]).resolve() == (tmp_path / "honeypot" / "data" / "lib").resolve()
    ), lib_mount
    assert not lib_mount.get("read_only"), lib_mount

    assert config.get("volumes") in (None, {}), config.get("volumes")


def test_cowrie_cfg_is_bind_mounted_read_only(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'config mount' row (line 151; Produces block line 83): the
    sensor config is bind-mounted read-only — a writable mount (the mutant this test kills) would
    let a fully-compromised Cowrie process (brief Goal, line 19: the host 'never holds ... any
    secret'; PRD §10.4: 'assume it will be fully compromised') rewrite its own sensor name or
    disable JSON logging.
    """
    config = _render_compose_config(tmp_path)
    cowrie = config["services"]["cowrie"]
    mounts = [
        mount
        for mount in cowrie.get("volumes", [])
        if mount.get("target") == "/cowrie/cowrie-git/etc/cowrie.cfg"
    ]
    assert mounts, cowrie.get("volumes")
    assert mounts[0]["read_only"] is True, mounts[0]


def test_cowrie_has_no_env_file_or_environment(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'no secrets' row (line 152; Produces block line 86: 'NO
    env_file, NO environment secrets'): `cowrie` carries neither an `env_file:` directive nor any
    rendered `environment` entries. Rendered against `_ENV_FILE_LEAK_CANARY` rather than an empty
    `.env` (module docstring): `docker compose config` folds a service's `env_file:` variables
    into its rendered `environment` dict but never renders the `env_file` key itself, so against
    an empty `.env` a leaked `env_file: ../.env` would add zero observable keys and this test
    would not catch it — the canary makes a leaked `env_file` observable without ever exercising
    a real secret.
    """
    config = _render_compose_config(tmp_path, env_text=_ENV_FILE_LEAK_CANARY)
    cowrie = config["services"]["cowrie"]
    assert "env_file" not in cowrie, cowrie
    assert cowrie.get("environment") in (None, {}, []), cowrie.get("environment")


def test_cowrie_restarts_and_rotates_logs(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'restart + logging' row (line 153; Produces block lines
    73, 84-85): `cowrie` restarts unattended after a reboot (`unless-stopped` — no human ever
    logs into this SSM-only, no-`sshd` box to restart it by hand) and carries the same
    `x-logging` shape (`json-file`, `max-size: 10m`, `max-file: 3`) as `infra/docker-compose.yml`
    (`.claude/rules/infra.md`: 'every production service sets json-file log rotation') — an
    unbounded Cowrie log would eventually fill the disk with no one watching.
    """
    config = _render_compose_config(tmp_path)
    cowrie = config["services"]["cowrie"]
    assert cowrie["restart"] == "unless-stopped", cowrie.get("restart")
    logging = cowrie["logging"]
    assert logging["driver"] == "json-file", logging
    assert logging["options"]["max-size"] == "10m", logging["options"]
    assert logging["options"]["max-file"] == "3", logging["options"]


def test_cowrie_image_is_the_official_image_tag_or_digest(tmp_path: Path) -> None:
    """Brief Interfaces → test table, 'image pin form' row (fix-1, review M2: digest-only —
    `image:` matches `^cowrie/cowrie@sha256:[0-9a-f]{64}$`): now that
    `honeypot/docker-compose.yml` is digest-pinned, the moving `cowrie/cowrie:latest` tag must
    fail this test too — 'never a moving tag on the box' (`.claude/rules/infra.md`) applies to
    third-party images the same as it does to this repo's own. Never a bare `cowrie/cowrie` with
    no tag or digest, and never any other image (the mutant this test kills: a copy-pasted
    `redis:7-alpine`, or a regression back to the moving `:latest` tag).
    """
    config = _render_compose_config(tmp_path)
    image = config["services"]["cowrie"]["image"]
    assert _IMAGE_PIN_RE.match(image), image


def test_cowrie_cfg_enables_jsonlog_and_names_the_sensor() -> None:
    """Brief Interfaces → test table, 'cfg' row (line 155; Produces block lines 92-100):
    `cowrie.cfg` enables the JSON output plugin and points it at a file ending `cowrie.json`, and
    names the deployed sensor `hp-use-01` (matching `honeypot/assets.yaml` and the alert's
    `sensor` field — PRD §1.2: 'one alert = one Cowrie session'). Parsed with
    `interpolation=None` (module docstring) since `logfile = ${honeypot:log_path}/cowrie.json` is
    Cowrie's own cross-section reference syntax, not configparser's — the mutant this guards
    against is a naive `ConfigParser()` call that would raise on this exact line instead of
    parsing it.
    """
    parser = _parse_cowrie_cfg()
    assert parser.get("output_jsonlog", "enabled") == "true"
    assert parser.get("output_jsonlog", "logfile").endswith("cowrie.json")
    assert parser.get("honeypot", "sensor_name") == "hp-use-01"


def test_assets_yaml_marks_the_deployed_sensor_and_keeps_twenty() -> None:
    """Brief Interfaces → test table, 'assets' row (line 156; Produces block lines 133-135): the
    header comment names `hp-use-01` as the deployed sensor (distinguishing it from the 19
    synthetic entries `fixtures/alerts/` and `evals/golden/` use), and the fleet still holds
    exactly 20 entries with `hp-use-01`'s existing `ssh-honeypot` role untouched —
    `tests/test_asset_info_tool.py::test_assets_yaml_covers_every_fixture_and_golden_sensor`
    already pins the 20-host shape and every role/exposure/criticality value; this test adds only
    the header-comment pin task-01 introduces and must not weaken or duplicate that file's
    assertions. The mutant this test kills: a header edit that mentions the deployment without
    ever naming `hp-use-01`, leaving which sensor is real ambiguous to a reader.
    """
    header, assets = _read_assets_yaml()
    assert "hp-use-01" in header, header
    assert "deployed" in header, header
    assert len(assets) == 20, sorted(assets)
    assert assets["hp-use-01"]["role"] == "ssh-honeypot", assets["hp-use-01"]


def test_honeypot_data_is_git_and_docker_ignored() -> None:
    """Brief Interfaces → test table, 'ignore claims' row (fix-1, review M3):
    `honeypot/docker-compose.yml`'s comments claim `honeypot/data/` is "gitignored AND
    dockerignored" — this test pins that claim against the two files that actually enforce it.
    `.gitignore` keeps the smoke artifacts (attacker host keys, raw `cowrie.json`) out of commits;
    `.dockerignore` is what keeps them out of the `api`/`worker` image build context (both build
    with the repo root as context — `infra/docker-compose.yml`). The mutant this test kills:
    either ignore line being deleted while the compose comment still claims it is there.
    """
    gitignore_text = (_REPO_ROOT / ".gitignore").read_text()
    dockerignore_text = (_REPO_ROOT / ".dockerignore").read_text()
    assert "honeypot/data/" in gitignore_text.splitlines(), gitignore_text
    assert "honeypot/data/" in dockerignore_text.splitlines(), dockerignore_text


def test_honeypot_readme_names_the_hardening_steps() -> None:
    """Brief Interfaces → test table, 'runbook' row (line 157; Produces block lines 102-131): the
    runbook text names the hardening steps task-05 depends on — `sshd` masked (step 3),
    `AmazonSSMManagedInstanceCore` as the (only) instance role (step 1), the 443-only egress
    security group (step 2), and the digest-pin form `@sha256` (step 4) — and never embeds a real
    `INGEST_HMAC_SECRET` value (`.claude/rules/infra.md`: no secret value anywhere in the repo).
    `re.search(r"INGEST_HMAC_SECRET=\\S+", text) is None` catches a well-meaning but
    rule-breaking 'example' value pasted into the runbook while still allowing the bare secret
    *name* the brief's step 6 requires ("The only secret ever placed here is the shipper's
    `INGEST_HMAC_SECRET`", line 131).
    """
    assert _README_FILE.exists(), (
        f"{_README_FILE} does not exist yet — task-01's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    text = _README_FILE.read_text()
    assert "systemctl mask sshd" in text
    assert "AmazonSSMManagedInstanceCore" in text
    assert "443" in text
    assert "@sha256" in text
    assert re.search(r"INGEST_HMAC_SECRET=\S+", text) is None
