"""Pins `infra/docker-compose.yml` against the m2 task-05 brief's Interfaces block: loopback-only
port publication, an `api` that waits on a healthy `postgres`, no migration hidden in a
`command:`, and a named (non-anonymous) Postgres volume (CONVENTIONS.md §11, PRD §11,
`.claude/rules/infra.md`).

`pyyaml` is not an installed runtime dependency here (only `types-pyyaml` is, for mypy), so these
tests render the compose file with `docker compose ... config --format json` and parse the result
with the stdlib `json` module rather than `yaml.safe_load`.

`infra/docker-compose.yml` declares `env_file: ../.env`, so `docker compose config` fails without
a `.env` next to the compose file's parent directory even though nothing in it is read. Every test
below renders the compose file the same way: copy it into a throwaway `tmp_path/infra/`, drop an
empty `tmp_path/.env` beside it, and run `config` from `tmp_path` — never against the real repo
root (which may or may not have a dev `.env`, and must never be mutated by a test run).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_FILE = _REPO_ROOT / "infra" / "docker-compose.yml"


def _render_compose_config(tmp_path: Path) -> dict[str, Any]:
    """Renders `infra/docker-compose.yml` with `docker compose config --format json` from a
    throwaway `tmp_path` copy and returns the parsed config. Skips (by name, not a silent pass)
    when `docker` is not on PATH, so every compose test in this module skips together.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH")

    assert _COMPOSE_FILE.exists(), (
        f"{_COMPOSE_FILE} does not exist yet — task-05's GREEN step creates it per the brief's "
        "Interfaces block."
    )

    tmp_infra = tmp_path / "infra"
    tmp_infra.mkdir()
    tmp_compose = tmp_infra / "docker-compose.yml"
    shutil.copy(_COMPOSE_FILE, tmp_compose)
    (tmp_path / ".env").write_text("")

    proc = subprocess.run(
        ["docker", "compose", "-f", str(tmp_compose), "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "sentinelbrief-test"},
    )
    assert proc.returncode == 0, f"docker compose config failed:\n{proc.stdout}\n{proc.stderr}"
    config: dict[str, Any] = json.loads(proc.stdout)
    return config


@pytest.fixture
def rendered_compose_config(tmp_path: Path) -> dict[str, Any]:
    """Thin fixture wrapper around `_render_compose_config` for the tests that only need the
    parsed config, not the `tmp_path` it was rendered from.
    """
    return _render_compose_config(tmp_path)


def test_compose_config_validates(rendered_compose_config: dict[str, Any]) -> None:
    """PRD §11 / brief Interfaces: `docker compose -f infra/docker-compose.yml config` exits 0
    and declares both the `postgres` and `api` services — a reference error (e.g. a typo'd
    service name in `depends_on`) fails `config` before `docker compose up` is ever attempted.
    """
    assert "postgres" in rendered_compose_config["services"]
    assert "api" in rendered_compose_config["services"]


def test_compose_publishes_loopback_only(rendered_compose_config: dict[str, Any]) -> None:
    """`.claude/rules/infra.md`: dev compose publishes ports on 127.0.0.1 only — a bare
    `"8000:8000"` mapping would expose the API (and Postgres) on every interface of the dev host.
    """
    services = rendered_compose_config["services"]
    published_targets: dict[str, set[str]] = {}
    for name, service in services.items():
        for port in service.get("ports", []):
            assert port.get("host_ip") == "127.0.0.1", (
                f"service {name!r} publishes {port} without binding to 127.0.0.1"
            )
            published_targets.setdefault(name, set()).add(str(port["target"]))

    assert published_targets.get("api") == {"8000"}, published_targets.get("api")
    assert published_targets.get("postgres") == {"5432"}, published_targets.get("postgres")


def test_compose_api_depends_on_healthy_postgres(rendered_compose_config: dict[str, Any]) -> None:
    """CONVENTIONS.md §11: `api` must wait for Postgres's healthcheck, not merely for the
    container to start, or the first DB connection (an inline triage or an `alembic upgrade`)
    races Postgres's own startup.
    """
    api = rendered_compose_config["services"]["api"]
    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"

    postgres = rendered_compose_config["services"]["postgres"]
    healthcheck_test = " ".join(postgres["healthcheck"]["test"])
    assert "pg_isready" in healthcheck_test, (
        f"postgres healthcheck does not use pg_isready: {healthcheck_test}"
    )


def test_compose_api_command_has_no_migration(rendered_compose_config: dict[str, Any]) -> None:
    """`.claude/rules/infra.md`: migrations never run at container start. The api service's
    `command:` (if it overrides the image's `CMD` at all) must not shell out to `alembic`; the
    documented path is `docker compose run --rm api uv run alembic upgrade head`.
    """
    command = rendered_compose_config["services"]["api"].get("command")
    if command is None:
        return
    text = " ".join(command) if isinstance(command, list) else str(command)
    assert "alembic" not in text.lower(), f"api service command runs a migration: {text}"


def test_compose_api_build_context_is_repo_root(tmp_path: Path) -> None:
    """`.claude/rules/infra.md`: images build from the repo root context
    (`build: {context: .., dockerfile: infra/...}`), never from `infra/` itself — a `context: .`
    typo (task-05 review I2 / mutation M10) would break the build outright, since `COPY
    pyproject.toml uv.lock ./` has no `pyproject.toml` to find one directory down. Rendered from
    its own `tmp_path` copy (not the shared `rendered_compose_config` fixture) so `context: ..`
    can be checked against the exact directory it resolves relative to.
    """
    config = _render_compose_config(tmp_path)
    build = config["services"]["api"]["build"]

    context = build["context"]
    assert Path(context).resolve() == tmp_path.resolve(), (
        f"api build context does not resolve to the repo root stand-in {tmp_path}: {context}"
    )
    dockerfile = build["dockerfile"].replace("\\", "/")
    assert dockerfile.endswith("infra/Dockerfile.api"), dockerfile


def test_compose_named_volume(rendered_compose_config: dict[str, Any]) -> None:
    """PRD §11: Postgres data must survive a `docker compose down` (without `-v`) via a named
    volume — an anonymous volume would be silently orphaned on the next container recreation.
    """
    assert "sentinelbrief_pg" in rendered_compose_config["volumes"], rendered_compose_config[
        "volumes"
    ]

    postgres = rendered_compose_config["services"]["postgres"]
    mounts = {(v["source"], v["target"]) for v in postgres.get("volumes", [])}
    assert ("sentinelbrief_pg", "/var/lib/postgresql/data") in mounts, mounts
