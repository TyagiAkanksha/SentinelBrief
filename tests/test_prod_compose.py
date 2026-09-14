"""Pins `infra/deploy/prod/docker-compose.yml` against the m6 task-03 brief's Interfaces block:
exactly six services, only Caddy publishes a host port, secrets reach only the containers that
need them (`api`/`worker` from `.env`, `postgres` from its own one-variable `.env.postgres`), the
non-secret shared env block, git-SHA ECR image tags with `api`/`worker` sharing one, restart +
log-rotation on every service, and the per-service shapes (worker, redis, postgres, caddy, web)
(PRD §11, §10.10, `.claude/rules/infra.md`).

`infra/deploy/prod/*` is a synced copy of what runs on the box at `/opt/sentinelbrief/` — every
bind mount and `env_file:` in the file is an absolute `/opt/sentinelbrief/...` path, not a
`context:`-relative one (this compose file has no `build:` sections at all). This module's helper
therefore does not need a `tmp_path/infra/...` layout the way `tests/test_compose_config.py`'s
does: it rewrites every literal `/opt/sentinelbrief/` to a throwaway `<tmp>/opt/sentinelbrief/`
directory it populates with a non-secret canary `.env`, a non-secret canary `.env.postgres`, an
empty `Caddyfile`, and an empty `geoip/` — exactly what the six bind mounts / env files need to
exist for `docker compose config` to resolve. The canary values make a misrouted `env_file:` (the
row this module's `test_env_file_reaches_api_and_worker_only_and_pg_gets_its_own` pins) observable
without ever exercising a real secret (rule 7 / `tests/test_compose_config.py`'s own
`_ENV_FILE_LEAK_CANARY` precedent — not imported across files, copied and adapted per the brief).

`docker compose ... config` interpolates the rendered `.env`/`.env.postgres` into its stdout
(`.claude/rules/infra.md`); the helper's failure message below therefore carries `proc.stderr`
ONLY, never `proc.stdout`, and no test in this module ever prints the parsed config.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROD_COMPOSE_FILE = _REPO_ROOT / "infra" / "deploy" / "prod" / "docker-compose.yml"

_OPT_PREFIX = "/opt/sentinelbrief/"
_ENV_CANARY = "SENTINELBRIEF_TEST_CANARY=canary\n"
_PG_ENV_CANARY = "SENTINELBRIEF_PG_CANARY=pgcanary\n"

_SHARED_ENV_KEYS = (
    "ENVIRONMENT",
    "REDIS_URL",
    "LLM_BASE_URL",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "GEOIP_DB_PATH",
    "GEOIP_ASN_DB_PATH",
    "ASSETS_YAML_PATH",
)

_ECR_IMAGE_RE = re.compile(
    r"^\d{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com/sentinelbrief/(api|web):[0-9a-f]{7,40}$"
)


def _render_prod_compose_config(tmp_path: Path) -> dict[str, Any]:
    """Renders `infra/deploy/prod/docker-compose.yml` from a throwaway copy in which every
    literal `/opt/sentinelbrief/` is rewritten to `<tmp_path>/opt/sentinelbrief/`, a directory this
    helper populates with the two canary env files, an empty `Caddyfile`, and an empty `geoip/` —
    everything the six services' bind mounts / `env_file:` directives point at. Skips (by name,
    not a silent pass) when `docker` is not on PATH, mirroring
    `tests/test_compose_config.py::_render_compose_config`.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH")

    assert _PROD_COMPOSE_FILE.exists(), (
        f"{_PROD_COMPOSE_FILE} does not exist yet — task-03's GREEN step creates it per the "
        "brief's Interfaces block."
    )

    opt_dir = tmp_path / "opt" / "sentinelbrief"
    opt_dir.mkdir(parents=True)
    (opt_dir / ".env").write_text(_ENV_CANARY)
    (opt_dir / ".env.postgres").write_text(_PG_ENV_CANARY)
    (opt_dir / "Caddyfile").write_text("")
    (opt_dir / "geoip").mkdir()

    original = _PROD_COMPOSE_FILE.read_text()
    rewritten = original.replace(_OPT_PREFIX, f"{opt_dir}/")
    assert rewritten != original, (
        f"{_PROD_COMPOSE_FILE} contains no literal {_OPT_PREFIX!r} to rewrite — did the file move?"
    )
    tmp_compose = tmp_path / "docker-compose.yml"
    tmp_compose.write_text(rewritten)

    proc = subprocess.run(
        ["docker", "compose", "-f", str(tmp_compose), "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "sentinelbrief-prod-test"},
    )
    # `.claude/rules/infra.md`: never paste rendered `docker compose config` output (which
    # interpolates the canary `.env`/`.env.postgres` here, a real secret on the box) anywhere —
    # `proc.stderr` only, never `proc.stdout`.
    assert proc.returncode == 0, f"docker compose config failed:\n{proc.stderr}"
    config: dict[str, Any] = json.loads(proc.stdout)
    return config


@pytest.fixture
def rendered_prod_compose_config(tmp_path: Path) -> dict[str, Any]:
    """Thin fixture wrapper around `_render_prod_compose_config` for tests that only need the
    parsed config, not the `tmp_path` it was rendered from.
    """
    return _render_prod_compose_config(tmp_path)


def test_prod_compose_validates_with_exactly_six_services(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: exactly `caddy`, `web`, `api`, `worker`, `postgres`, `redis` — task-04's
    backup job is a host-level systemd timer, never a seventh compose service.
    """
    assert set(rendered_prod_compose_config["services"]) == {
        "caddy",
        "web",
        "api",
        "worker",
        "postgres",
        "redis",
    }, rendered_prod_compose_config["services"].keys()


def test_only_caddy_publishes_ports_80_and_443(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """`.claude/rules/infra.md`: production compose never host-publishes `api`, `worker`,
    `postgres` or `redis` — only Caddy's 80/443 are exposed. A `ports:` line accidentally left on
    any other service would put a backend container directly on the internet.
    """
    services = rendered_prod_compose_config["services"]

    caddy_ports = {str(port["published"]) for port in services["caddy"].get("ports", [])}
    assert caddy_ports == {"80", "443"}, caddy_ports

    for name, service in services.items():
        if name == "caddy":
            continue
        assert not service.get("ports"), f"{name} publishes a port: {service.get('ports')}"


def test_env_file_reaches_api_and_worker_only_and_pg_gets_its_own(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: `api`/`worker` load `/opt/sentinelbrief/.env` (the app secrets);
    `postgres` loads its own `/opt/sentinelbrief/.env.postgres` (`POSTGRES_PASSWORD` only); `web`,
    `caddy`, `redis` load neither — the canary values make a misrouted `env_file:` observable
    without ever exercising a real secret.
    """
    services = rendered_prod_compose_config["services"]

    assert services["api"]["environment"].get("SENTINELBRIEF_TEST_CANARY") == "canary"
    assert services["worker"]["environment"].get("SENTINELBRIEF_TEST_CANARY") == "canary"
    for name in ("web", "caddy", "redis", "postgres"):
        assert "SENTINELBRIEF_TEST_CANARY" not in services[name].get("environment", {}), name

    assert services["postgres"]["environment"].get("SENTINELBRIEF_PG_CANARY") == "pgcanary"
    for name in ("web", "caddy", "redis", "api", "worker"):
        assert "SENTINELBRIEF_PG_CANARY" not in services[name].get("environment", {}), name


def test_api_and_worker_share_the_pinned_non_secret_env(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: `x-shared-env` is identical between `api` and `worker`; `ENVIRONMENT` is
    pinned `production`; `REDIS_URL` carries no password on the compose network (PRD §11);
    `CORS_ORIGINS` is `https://` with no wildcard; `FORWARDED_ALLOW_IPS=*` is present on `api`
    only — it is safe there solely because Caddy overwrites `X-Forwarded-For` AND `api` is never
    host-published (PRD §10.10), a pairing this test does not itself re-verify (see
    `test_only_caddy_publishes_ports_80_and_443` and `tests/test_prod_caddyfile.py`).
    """
    services = rendered_prod_compose_config["services"]
    api_env = services["api"]["environment"]
    worker_env = services["worker"]["environment"]

    for key in _SHARED_ENV_KEYS:
        assert api_env.get(key) == worker_env.get(key), (key, api_env.get(key), worker_env.get(key))

    assert api_env["ENVIRONMENT"] == "production"
    assert api_env["REDIS_URL"] == "redis://redis:6379/0"
    assert api_env["CORS_ORIGINS"].startswith("https://"), api_env["CORS_ORIGINS"]
    assert "*" not in api_env["CORS_ORIGINS"]

    assert api_env["FORWARDED_ALLOW_IPS"] == "*"
    assert "FORWARDED_ALLOW_IPS" not in worker_env, worker_env.get("FORWARDED_ALLOW_IPS")


def test_images_are_ecr_sha_tags_and_api_worker_share_one(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: every image is a 12-digit-account-id ECR URI tagged with a 7-40 char hex
    SHA (never `latest`); `worker` shares `api`'s exact image (same account/SHA); `web`'s tag
    equals `api`'s tag (both bump together on every deploy, prod/README.md); no service declares a
    `build:` section (a build-from-source service would defeat the whole point of pushing pinned
    images first).
    """
    services = rendered_prod_compose_config["services"]

    for name in ("web", "api", "worker"):
        image = services[name]["image"]
        assert _ECR_IMAGE_RE.match(image), (
            f"{name} image does not match the ECR SHA-tag pattern: {image}"
        )

    assert services["api"]["image"] == services["worker"]["image"]

    api_tag = services["api"]["image"].rsplit(":", 1)[1]
    web_tag = services["web"]["image"].rsplit(":", 1)[1]
    assert api_tag == web_tag, (api_tag, web_tag)

    for name, service in services.items():
        assert "latest" not in service.get("image", ""), name
        assert "build" not in service, f"{name} declares a build: section: {service.get('build')}"


def test_every_service_restarts_unless_stopped_and_rotates_logs(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: all six services restart `unless-stopped` (distinguishable from the dev
    compose file's `on-failure`, pinned by
    `tests/test_compose_config.py::test_compose_every_service_has_restart_on_failure` — rule 7)
    and rotate json-file logs at `10m`/`3` files each, from day one (PRD §11).
    """
    for name, service in rendered_prod_compose_config["services"].items():
        assert service.get("restart") == "unless-stopped", (name, service.get("restart"))
        logging = service.get("logging", {})
        assert logging.get("driver") == "json-file", name
        assert logging.get("options", {}).get("max-size") == "10m", name
        assert logging.get("options", {}).get("max-file") == "3", name


def test_worker_shape(rendered_prod_compose_config: dict[str, Any]) -> None:
    """Brief Interfaces: `worker`'s `command:` overrides the shared api image to run ARQ; its
    healthcheck runs `arq --check`; it waits for a *healthy* `postgres` AND `redis`; its geoip bind
    mount is read-only, same as `api`'s.
    """
    worker = rendered_prod_compose_config["services"]["worker"]

    assert worker["command"] == ["arq", "worker.main.WorkerSettings"]

    healthcheck_test = " ".join(worker["healthcheck"]["test"])
    assert "arq --check" in healthcheck_test, healthcheck_test

    assert worker["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["redis"]["condition"] == "service_healthy"

    geoip_mounts = [
        mount for mount in worker.get("volumes", []) if mount.get("target") == "/app/infra/geoip"
    ]
    assert geoip_mounts, f"worker has no bind mount at /app/infra/geoip: {worker.get('volumes')}"
    assert geoip_mounts[0]["read_only"] is True


def test_redis_has_no_maxmemory_and_snapshots(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces / M5 ruling R14: the prod `redis` command is identical to the dev file's
    (`infra/docker-compose.yml`) — an RDB snapshot policy, no `maxmemory`/eviction flag, so a
    queued-but-not-yet-run ARQ job is never dropped to reclaim memory — on a named volume so the
    snapshot survives a container recreation.
    """
    redis = rendered_prod_compose_config["services"]["redis"]

    assert redis["command"] == ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    assert "maxmemory" not in " ".join(redis["command"])

    volumes = {(mount["source"], mount["target"]) for mount in redis.get("volumes", [])}
    assert ("sentinelbrief_redis", "/data") in volumes, volumes


def test_postgres_named_volume_and_healthcheck(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: Postgres data survives a `docker compose down` (without `-v`) via the
    named `sentinelbrief_pg` volume; the healthcheck uses `pg_isready`, same as dev.
    """
    postgres = rendered_prod_compose_config["services"]["postgres"]

    volumes = {(mount["source"], mount["target"]) for mount in postgres.get("volumes", [])}
    assert ("sentinelbrief_pg", "/var/lib/postgresql/data") in volumes, volumes

    healthcheck_test = " ".join(postgres["healthcheck"]["test"])
    assert "pg_isready" in healthcheck_test, healthcheck_test


def test_caddy_mounts_caddyfile_read_only_and_persists_data(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: Caddy's config comes from a read-only bind mount of the committed
    `Caddyfile` (so the running container can never rewrite the file that governs it), and its ACME
    state (`caddy_data`) persists on a named volume across restarts (no re-issuing a cert on every
    `docker compose up`).
    """
    caddy = rendered_prod_compose_config["services"]["caddy"]

    caddyfile_mounts = [
        mount for mount in caddy.get("volumes", []) if mount.get("target") == "/etc/caddy/Caddyfile"
    ]
    assert caddyfile_mounts, (
        f"caddy has no bind mount at /etc/caddy/Caddyfile: {caddy.get('volumes')}"
    )
    assert caddyfile_mounts[0]["read_only"] is True
    assert caddyfile_mounts[0]["source"].endswith("Caddyfile"), caddyfile_mounts[0]["source"]

    named_volumes = {
        (mount["source"], mount["target"])
        for mount in caddy.get("volumes", [])
        if mount.get("type") == "volume"
    }
    assert ("caddy_data", "/data") in named_volumes, named_volumes


def test_web_environment_is_exactly_hostname_port_api_url(
    rendered_prod_compose_config: dict[str, Any],
) -> None:
    """Brief Interfaces: `web` gets no `env_file:` (it never sees a backend secret — the one
    internet-facing SSR surface); its rendered environment is exactly `HOSTNAME`, `PORT`,
    `API_URL`, and `API_URL` points at the compose-network `api` service, not a public hostname.
    """
    web = rendered_prod_compose_config["services"]["web"]

    assert set(web["environment"]) == {"HOSTNAME", "PORT", "API_URL"}, web["environment"]
    assert web["environment"]["API_URL"] == "http://api:8000"
