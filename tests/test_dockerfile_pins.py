"""Pins `infra/Dockerfile.api` against the m2 task-05 brief's Interfaces block: a multi-stage
`uv` build that runs the API as a non-root user, ships a stdlib-only healthcheck, never bakes
`.env` (or any secret name) into the image, and never runs migrations at container start
(CONVENTIONS.md §11, `.claude/rules/infra.md`).

These are pure text pins over the Dockerfile's instructions — no `docker build`, no daemon
needed. `_instructions()` joins Docker's `\\`-continued lines into one logical instruction per
Dockerfile directive (HEALTHCHECK and CMD both span two source lines in the reference shape) so
the assertions below read the same instruction a `docker build` would.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _REPO_ROOT / "infra" / "Dockerfile.api"
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"

_SOURCE_COPIES = {"api", "worker", "core", "evals", "alembic", "alembic.ini"}
_FORBIDDEN_ENV_NAMES = ("LLM_API_KEY", "INGEST_HMAC_SECRET", "ADMIN_TOKEN", "DATABASE_URL")


def _instructions() -> list[str]:
    """Reads `infra/Dockerfile.api` and returns one string per logical Docker instruction,
    skipping blank lines and comments and joining `\\`-continued lines into a single block.
    """
    assert _DOCKERFILE.exists(), (
        f"{_DOCKERFILE} does not exist yet — task-05's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    blocks: list[str] = []
    buf = ""
    for raw in _DOCKERFILE.read_text().splitlines():
        stripped = raw.strip()
        if not buf:
            if not stripped or stripped.startswith("#"):
                continue
            buf = stripped
        else:
            buf = f"{buf} {stripped}"
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        blocks.append(buf)
        buf = ""
    if buf:
        blocks.append(buf)
    return blocks


def test_runs_as_non_root() -> None:
    """CONVENTIONS.md §11 / `.claude/rules/infra.md`: the runtime stage drops root — a container
    running as root that is ever mis-exposed gives an attacker a much easier escape.
    """
    instructions = _instructions()
    from_indices = [i for i, block in enumerate(instructions) if block.split()[0].upper() == "FROM"]
    assert from_indices, "no FROM instruction found"
    tail = instructions[from_indices[-1] :]

    assert any(block.strip() == "USER appuser" for block in tail), (
        "no `USER appuser` instruction after the last FROM"
    )
    assert any("useradd" in block and "1001" in block and "appuser" in block for block in tail), (
        "no `useradd ... --uid 1001 ... appuser` instruction after the last FROM"
    )


def test_healthcheck_present() -> None:
    """CONVENTIONS.md §11: a stdlib-only HEALTHCHECK against `/healthz` — no `curl` binary is
    installed or invoked anywhere in the image, keeping the runtime stage minimal.
    """
    instructions = _instructions()
    healthchecks = [block for block in instructions if block.split()[0].upper() == "HEALTHCHECK"]
    assert healthchecks, "no HEALTHCHECK instruction found"

    block = healthchecks[0]
    assert "http://127.0.0.1:8000/healthz" in block, (
        f"HEALTHCHECK does not target /healthz: {block}"
    )
    assert "urllib.request" in block, f"HEALTHCHECK does not use stdlib urllib.request: {block}"
    assert "python" in block, f"HEALTHCHECK CMD is not `python -c ...`: {block}"

    full_text = "\n".join(instructions).lower()
    assert "curl" not in full_text, (
        "curl referenced somewhere in the Dockerfile (stdlib-only healthcheck)"
    )


def test_no_env_file_copied() -> None:
    """CONVENTIONS.md §11 / `.claude/rules/infra.md`: runtime config is env-only. Neither a
    `COPY`/`ADD` of `.env` nor an `ENV` line hardcoding a secret name may ship inside the image —
    both would bake a value or a name-shaped seam into a layer that outlives any single container.
    """
    instructions = _instructions()

    for block in instructions:
        first = block.split()[0].upper()
        if first in ("COPY", "ADD"):
            assert ".env" not in block, f"{first} line references .env: {block}"

    for block in instructions:
        if block.split()[0].upper() != "ENV":
            continue
        for name in _FORBIDDEN_ENV_NAMES:
            assert name not in block, f"ENV line sets {name}: {block}"


def test_multistage_uv_builder() -> None:
    """Brief Interfaces: stage 1 (`uv` builder image) syncs locked deps before any source is
    copied (cacheable layer), then syncs the project itself once `api`, `worker`, `core`,
    `evals`, `alembic`, and `alembic.ini` are present; stage 2 is the slim runtime image. Getting
    the two `uv sync` calls in the wrong order around the source COPYs defeats the whole point of
    the two-step sync (every source edit would invalidate the locked-deps layer too).
    """
    instructions = _instructions()

    from_blocks = [block for block in instructions if block.split()[0].upper() == "FROM"]
    assert len(from_blocks) >= 2, "expected a builder stage and a runtime stage"
    assert "ghcr.io/astral-sh/uv:python3.12-bookworm-slim" in from_blocks[0], (
        f"builder stage is not the uv image: {from_blocks[0]}"
    )
    assert "as builder" in from_blocks[0].lower(), (
        f"builder stage is not named `builder`: {from_blocks[0]}"
    )
    assert "python:3.12-slim-bookworm" in from_blocks[-1], (
        f"runtime stage is not slim-bookworm: {from_blocks[-1]}"
    )

    deps_only_idx: int | None = None
    full_idx: int | None = None
    for i, block in enumerate(instructions):
        if block.split()[0].upper() != "RUN":
            continue
        if "uv sync --frozen --no-dev --no-install-project" in block:
            deps_only_idx = i
        elif "uv sync --frozen --no-dev" in block and "--no-install-project" not in block:
            full_idx = i
    assert deps_only_idx is not None, "no `RUN uv sync --frozen --no-dev --no-install-project`"
    assert full_idx is not None, "no final `RUN uv sync --frozen --no-dev` (installing the project)"

    copy_indices: list[int] = []
    found: set[str] = set()
    for i, block in enumerate(instructions):
        parts = block.split()
        if (
            len(parts) == 3
            and parts[0].upper() == "COPY"
            and parts[2] == f"./{parts[1]}"
            and parts[1] in _SOURCE_COPIES
        ):
            copy_indices.append(i)
            found.add(parts[1])
    assert found == _SOURCE_COPIES, f"missing source COPY line(s): {sorted(_SOURCE_COPIES - found)}"

    assert deps_only_idx < min(copy_indices), (
        "`uv sync --no-install-project` must run before the source COPYs (cache the deps layer)"
    )
    assert max(copy_indices) < full_idx, (
        "the final `uv sync --frozen --no-dev` must run after all source COPYs"
    )


def test_cmd_is_uvicorn_proxy_headers() -> None:
    """CONVENTIONS.md §11 / `.claude/rules/infra.md`: the image's `CMD` is uvicorn and nothing
    else — migrations are invoked explicitly, never at container start. Parsing the JSON-array
    form directly (rather than substring-matching) also rejects a shell-form `CMD "uvicorn ..."`,
    which would swallow signals differently under `docker stop`.
    """
    instructions = _instructions()
    cmd_blocks = [block for block in instructions if block.split()[0].upper() == "CMD"]
    assert cmd_blocks, "no CMD instruction found"

    json_part = cmd_blocks[-1][len("CMD") :].strip()
    parsed = json.loads(json_part)
    assert parsed == [
        "uvicorn",
        "api.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--proxy-headers",
    ], f"CMD is not the expected uvicorn invocation: {parsed}"

    cmd_or_entrypoint = [
        block for block in instructions if block.split()[0].upper() in ("CMD", "ENTRYPOINT")
    ]
    for block in cmd_or_entrypoint:
        assert "alembic upgrade" not in block.lower(), (
            f"migrations must never run at container start: {block}"
        )


def _runtime_copy_from_builder(tail: list[str]) -> list[tuple[str, str, str]]:
    """Returns `(source, dest, block)` for every `COPY --from=builder ...` instruction in `tail`,
    tolerant of flag order (`--chown=...` before or after `--from=builder`) so the pin doesn't
    over-fit incidental whitespace or flag ordering.
    """
    entries: list[tuple[str, str, str]] = []
    for block in tail:
        parts = block.split()
        if not parts or parts[0].upper() != "COPY":
            continue
        if not any(part.startswith("--from=builder") for part in parts):
            continue
        positional = [part for part in parts[1:] if not part.startswith("--")]
        if len(positional) != 2:
            continue
        source, dest = positional
        entries.append((source, dest, block))
    return entries


def test_runtime_stage_has_uv_binary() -> None:
    """Brief Interfaces: the runtime stage copies `/usr/local/bin/uv` from the builder — without
    it, the documented migration command (`docker compose run --rm api uv run alembic upgrade
    head`) has no `uv` binary to invoke inside the runtime image (task-05 review I2 / mutation
    M11: deleting this line breaks the only sanctioned migration path with every other pin green).
    """
    instructions = _instructions()
    from_indices = [i for i, block in enumerate(instructions) if block.split()[0].upper() == "FROM"]
    tail = instructions[from_indices[-1] :]

    entries = _runtime_copy_from_builder(tail)
    assert any(source == "/usr/local/bin/uv" for source, _, _ in entries), (
        "no `COPY --from=builder ... /usr/local/bin/uv ...` after the last FROM — "
        "`uv run alembic upgrade head` would not work inside the runtime image"
    )


def test_runtime_stage_copies_venv_and_sources() -> None:
    """Brief Interfaces: the runtime stage copies `.venv` plus each of the six sources from the
    builder, every one `--chown=appuser:appuser` (task-05 review I2 / mutation M12: deleting the
    runtime stage's source COPYs ships an image that cannot import `api.main` with every other
    pin green, because `test_multistage_uv_builder` only ever looks at the builder stage).
    """
    instructions = _instructions()
    from_indices = [i for i, block in enumerate(instructions) if block.split()[0].upper() == "FROM"]
    tail = instructions[from_indices[-1] :]

    entries = _runtime_copy_from_builder(tail)
    required_sources = {f"/app/{name}" for name in _SOURCE_COPIES} | {"/app/.venv"}
    found_sources = {source for source, _, _ in entries if source in required_sources}
    missing = sorted(required_sources - found_sources)
    assert found_sources == required_sources, (
        f"runtime stage is missing COPY --from=builder for: {missing}"
    )

    for source, _dest, block in entries:
        if source in required_sources:
            assert "--chown=appuser:appuser" in block, (
                f"runtime COPY of {source} is missing --chown=appuser:appuser: {block}"
            )


def test_prompts_not_dockerignored() -> None:
    """M1 review carry-over guard: `worker/prompts/*.md` must survive `.dockerignore`'s blanket
    `*.md` exclusion, or `worker.prompts.load_prompt` finds nothing inside the built image. This
    pins the existing `.dockerignore` negation rather than the (not-yet-created) Dockerfile, so
    it documents the guard alongside the image tests that depend on it.
    """
    assert _DOCKERIGNORE.exists(), f"{_DOCKERIGNORE} is missing"
    lines = [line.strip() for line in _DOCKERIGNORE.read_text().splitlines()]
    assert "!worker/prompts/*.md" in lines, (
        "`.dockerignore` is missing the `!worker/prompts/*.md` negation — "
        "`COPY worker ./worker` would ship without the prompt files"
    )
