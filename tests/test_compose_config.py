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

m3 task-07 extends this module with the `web` service (`test_compose_web_service_shape`) and adds
a `web` assertion to two of the m2-pinned tests below (`test_compose_config_validates`,
`test_compose_publishes_loopback_only`); no other assertion in this file changes.

m4 task-03 adds `test_compose_api_mounts_geoip_read_only`: the `api` service's read-only bind
mount of `infra/geoip` (the deploy-time `.mmdb` directory `scripts/fetch_geoip.py` populates).

m5 task-01 adds `redis` and `worker`: `test_compose_redis_service_shape`,
`test_compose_worker_service_shape`, `test_compose_api_depends_on_healthy_redis`, and extends
`test_compose_config_validates`/`test_compose_publishes_loopback_only` (the only two m2-pinned
tests below that change) with the two new services.

m6 task-03 adds `test_compose_every_service_has_restart_on_failure` (the dev-compose half of the
carried M5 item N-W1) and changes `_render_compose_config`'s failure-message assertion to report
`proc.stderr` ONLY, never `proc.stdout` — a `docker compose config` failure interpolates the
throwaway `.env`, so pasting `proc.stdout` into a CI log risks leaking it (`.claude/rules/infra.md`
task-01 review note). `tests/test_prod_compose.py`'s own helper is written that way from the
start.
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


def _render_compose_config(tmp_path: Path, *, env_text: str = "") -> dict[str, Any]:
    """Renders `infra/docker-compose.yml` with `docker compose config --format json` from a
    throwaway `tmp_path` copy and returns the parsed config. Skips (by name, not a silent pass)
    when `docker` is not on PATH, so every compose test in this module skips together.

    `env_text` seeds the throwaway `.env` this copy renders against; every existing caller relies
    on the empty default. `test_compose_web_service_shape` (m3 task-07 fix-1, review I3) passes a
    synthetic, non-secret canary line instead: `docker compose config` resolves a service's
    `env_file:` directive into its rendered `environment` dict and *never* renders the `env_file`
    key itself (verified empirically — see that test's docstring), so against an empty `.env` a
    stray `env_file: ../.env` on `web` would add zero keys and go undetected by an
    `environment`-shape assertion. The canary makes a leaked `env_file` observable without ever
    exercising a real secret.
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
    (tmp_path / ".env").write_text(env_text)

    proc = subprocess.run(
        ["docker", "compose", "-f", str(tmp_compose), "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "sentinelbrief-test"},
    )
    assert proc.returncode == 0, f"docker compose config failed:\n{proc.stderr}"
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
    and declares the `postgres`, `api`, `web`, `redis` and `worker` services (m3 task-07 adds
    `web`; m5 task-01 adds `redis`/`worker`) — a reference error (e.g. a typo'd service name in
    `depends_on`) fails `config` before `docker compose up` is ever attempted.
    """
    assert "postgres" in rendered_compose_config["services"]
    assert "api" in rendered_compose_config["services"]
    assert "web" in rendered_compose_config["services"]
    assert "redis" in rendered_compose_config["services"]
    assert "worker" in rendered_compose_config["services"]


def test_compose_publishes_loopback_only(rendered_compose_config: dict[str, Any]) -> None:
    """`.claude/rules/infra.md`: dev compose publishes ports on 127.0.0.1 only — a bare
    `"8000:8000"` mapping would expose the API (and Postgres) on every interface of the dev host.
    m3 task-07 adds the `web` service's port to this same check; m5 task-01 adds `redis`'s port
    and asserts `worker` publishes none at all (it is never addressed directly — only ARQ jobs
    reach it, through Redis).
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
    assert published_targets.get("web") == {"3000"}, published_targets.get("web")
    assert published_targets.get("redis") == {"6379"}, published_targets.get("redis")
    assert "worker" not in published_targets, published_targets


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


_ENV_FILE_LEAK_CANARY = "SENTINELBRIEF_TEST_CANARY=canary\n"
"""Synthetic, non-secret `.env` content for `test_compose_web_service_shape` only (review I3) —
see `_render_compose_config`'s `env_text` docstring for why a non-empty `.env` is required to
make a leaked `env_file: ../.env` on the `web` service observable at all."""


def test_compose_web_service_shape(tmp_path: Path) -> None:
    """m3 task-07 brief Interfaces: the `web` service builds from the repo-root context with
    `infra/Dockerfile.web`, bakes the browser-visible API origin in as a build `arg`, talks to
    `api` over the compose network server-side (`API_URL=http://api:8000`), waits for a *healthy*
    `api` before starting, and carries the same json-file log rotation as `postgres`/`api` — a
    bare `depends_on: [api]` (started, not healthy) would let the web container's own healthcheck
    race the API's boot. Rendered from its own `tmp_path` copy (not the shared
    `rendered_compose_config` fixture), mirroring `test_compose_api_build_context_is_repo_root`,
    so `context: ..` can be checked against the exact directory it resolves relative to.

    Review I3 (m3 task-07 fix-1): `web` is the one internet-facing SSR surface (PRD §9/§11 —
    Caddy fronts it); copy-pasting `api`'s `env_file: ../.env` onto `web` would put
    `LLM_API_KEY`/`INGEST_HMAC_SECRET`/`ADMIN_TOKEN`/`DATABASE_URL` into a Node process whose error
    pages and SSR bugs are reachable from the public internet. `set(web["environment"])` pins that
    `API_URL` is the *only* rendered environment key. Rendered against
    `_ENV_FILE_LEAK_CANARY` (a synthetic, non-secret line) rather than the module's usual empty
    `.env`: `docker compose config` folds a service's `env_file:` variables into its rendered
    `environment` dict but never renders an `env_file` key at all (verified empirically — see
    `_render_compose_config`'s docstring), so against an empty `.env` a leaked `env_file: ../.env`
    on `web` would add zero observable keys and this assertion would not catch it. The
    `"env_file" not in web` assertion below is therefore not load-bearing on its own (compose
    never renders that key, mutated or not) — it is kept as source-adjacent documentation of the
    same intent, not as the catch.
    """
    config = _render_compose_config(tmp_path, env_text=_ENV_FILE_LEAK_CANARY)
    web = config["services"]["web"]

    assert web["image"] == "sentinelbrief-web"
    assert web["environment"]["API_URL"] == "http://api:8000"
    assert set(web["environment"]) == {"API_URL"}, web["environment"]
    assert web["depends_on"]["api"]["condition"] == "service_healthy"

    build = web["build"]
    context = build["context"]
    assert Path(context).resolve() == tmp_path.resolve(), (
        f"web build context does not resolve to the repo root stand-in {tmp_path}: {context}"
    )
    dockerfile = build["dockerfile"].replace("\\", "/")
    assert dockerfile.endswith("infra/Dockerfile.web"), dockerfile
    assert "NEXT_PUBLIC_API_URL" in build.get("args", {}), build.get("args")

    logging = web["logging"]
    assert logging["driver"] == "json-file"
    assert logging["options"]["max-size"] == "10m"


def test_compose_api_mounts_geoip_read_only(tmp_path: Path) -> None:
    """m4 task-03 brief Interfaces: the `api` service bind-mounts `infra/geoip` read-only at
    `/app/infra/geoip` — the same relative path `GEOIP_DB_PATH`/`GEOIP_ASN_DB_PATH` resolve to on
    the host and in the container — so `scripts/fetch_geoip.py`'s deploy-time output is reachable
    from inside the container but never writable there. Rendered from its own `tmp_path` copy
    (not the shared `rendered_compose_config` fixture), mirroring
    `test_compose_api_build_context_is_repo_root`, so the bind's relative `../infra/geoip` source
    can be checked against the exact directory it resolves relative to.
    """
    config = _render_compose_config(tmp_path)
    api = config["services"]["api"]

    geoip_mounts = [
        mount for mount in api.get("volumes", []) if mount.get("target") == "/app/infra/geoip"
    ]

    assert geoip_mounts, f"api has no bind mount at /app/infra/geoip: {api.get('volumes')}"
    mount = geoip_mounts[0]
    assert mount["read_only"] is True
    assert Path(mount["source"]).resolve() == (tmp_path / "infra" / "geoip").resolve()


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


def test_compose_redis_service_shape(rendered_compose_config: dict[str, Any]) -> None:
    """m5 task-01 brief Interfaces: `redis:7-alpine`, loopback-only, an RDB snapshot policy (the
    `command:` override — fix-1 M2: the docstring alone overclaimed this pin, no assertion below
    it actually checked the snapshot policy) so queued jobs survive a restart, a named
    (non-anonymous) volume, a `redis-cli ping` healthcheck, and the same json-file log rotation
    as every other service.
    """
    redis = rendered_compose_config["services"]["redis"]

    assert redis["image"] == "redis:7-alpine"
    assert redis["command"] == ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    ports = redis.get("ports", [])
    assert len(ports) == 1
    assert ports[0]["host_ip"] == "127.0.0.1"
    assert str(ports[0]["target"]) == "6379"

    healthcheck_test = " ".join(redis["healthcheck"]["test"])
    assert "redis-cli ping" in healthcheck_test

    mounts = {(v["source"], v["target"]) for v in redis.get("volumes", [])}
    assert ("sentinelbrief_redis", "/data") in mounts

    assert "sentinelbrief_redis" in rendered_compose_config["volumes"]

    logging = redis["logging"]
    assert logging["driver"] == "json-file"
    assert logging["options"]["max-size"] == "10m"


def test_compose_worker_service_shape(tmp_path: Path) -> None:
    """m5 task-01 brief Interfaces: `worker` shares the api image (same build context/Dockerfile,
    same `image:` tag) with a different `command:`, publishes no ports, mounts `infra/geoip`
    read-only (M4 task-03 M6: the worker is where `get_ip_geo_asn` runs now), waits for a
    *healthy* `postgres` AND `redis`, and its own healthcheck runs `arq --check`. Rendered against
    `_ENV_FILE_LEAK_CANARY` (like `test_compose_web_service_shape`) — but unlike `web`, `worker`
    legitimately needs every secret in `.env` (the LLM key, the DB URL, the Redis URL), so this
    proves `env_file: ../.env` really is applied to it, not merely absent. Rendered from its own
    `tmp_path` copy (not the shared `rendered_compose_config` fixture), mirroring
    `test_compose_api_build_context_is_repo_root`, so the build context can be checked against
    the exact directory it resolves relative to.
    """
    config = _render_compose_config(tmp_path, env_text=_ENV_FILE_LEAK_CANARY)
    worker = config["services"]["worker"]

    assert worker["command"] == ["arq", "worker.main.WorkerSettings"]
    assert not worker.get("ports")
    assert worker["image"] == "sentinelbrief-api"
    assert worker["environment"]["SENTINELBRIEF_TEST_CANARY"] == "canary"

    build = worker["build"]
    assert Path(build["context"]).resolve() == tmp_path.resolve(), (
        f"worker build context does not resolve to the repo-root stand-in {tmp_path}: "
        f"{build['context']}"
    )
    dockerfile = build["dockerfile"].replace("\\", "/")
    assert dockerfile.endswith("infra/Dockerfile.api"), dockerfile

    geoip_mounts = [
        mount for mount in worker.get("volumes", []) if mount.get("target") == "/app/infra/geoip"
    ]
    assert geoip_mounts, f"worker has no bind mount at /app/infra/geoip: {worker.get('volumes')}"
    assert geoip_mounts[0]["read_only"] is True

    assert worker["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["redis"]["condition"] == "service_healthy"

    healthcheck_test = " ".join(worker["healthcheck"]["test"])
    assert "arq --check worker.main.WorkerSettings" in healthcheck_test

    logging = worker["logging"]
    assert logging["driver"] == "json-file"
    assert logging["options"]["max-size"] == "10m"


def test_compose_api_depends_on_healthy_redis(rendered_compose_config: dict[str, Any]) -> None:
    """m5 task-01: `api` must wait for a *healthy* `redis` too, not just a healthy `postgres` —
    the ingest route enqueues on every create now, so a `redis` that has merely started (but
    isn't accepting connections yet) would race the first request the same way an unready
    Postgres would (`test_compose_api_depends_on_healthy_postgres`, unchanged above).
    """
    api = rendered_compose_config["services"]["api"]
    assert api["depends_on"]["redis"]["condition"] == "service_healthy"


def test_compose_every_service_has_restart_on_failure(
    rendered_compose_config: dict[str, Any],
) -> None:
    """m6 task-03 carried M5 item (walk finding N-W1): every dev service restarts `on-failure` —
    e.g. the ARQ worker exiting 1 when Redis vanished — without auto-starting six containers at
    every dev-machine boot the way `unless-stopped` would. Distinguishable on purpose from the
    production compose file's `unless-stopped` (rule 7), pinned separately by
    `tests/test_prod_compose.py::test_every_service_restarts_unless_stopped_and_rotates_logs`.
    """
    for name, service in rendered_compose_config["services"].items():
        assert service.get("restart") == "on-failure", (name, service.get("restart"))
