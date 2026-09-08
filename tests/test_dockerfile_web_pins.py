"""Pins `infra/Dockerfile.web` against the m3 task-07 brief's Interfaces block: a two-stage
`node:24-slim` build that installs the workspace with pnpm, builds the Next 16 standalone output,
and runs it as a non-root `nextjs` (uid 1001) user with a stdlib `node` healthcheck against
`/healthz` — never baking `.env` or a secret name into the image (CONVENTIONS.md §11,
`.claude/rules/infra.md`).

These are pure text pins over the Dockerfile's instructions — no `docker build`, no daemon
needed. `_instructions()` mirrors `tests/test_dockerfile_pins.py`'s parser: it joins Docker's
`\\`-continued lines into one logical instruction per Dockerfile directive (HEALTHCHECK spans two
source lines in the reference shape) so the assertions below read the same instruction a `docker
build` would.

The observed Next 16 standalone layout (task-03 implementer report,
`.superpowers/sdd/m3-read-path-dashboard/task-03-implementer.md`, verified by its reviewer) is
nested: `web/.next/standalone/web/server.js` — the `CMD` and the standalone `COPY` destinations
below are pinned to that layout, not the flat one the brief flagged as an alternative.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _REPO_ROOT / "infra" / "Dockerfile.web"

_FORBIDDEN_ENV_NAMES = ("LLM_API_KEY", "INGEST_HMAC_SECRET", "ADMIN_TOKEN", "DATABASE_URL")


def _instructions() -> list[str]:
    """Reads `infra/Dockerfile.web` and returns one string per logical Docker instruction,
    skipping blank lines and comments and joining `\\`-continued lines into a single block.
    """
    assert _DOCKERFILE.exists(), (
        f"{_DOCKERFILE} does not exist yet — task-07's GREEN step creates it per the brief's "
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


def _from_indices(instructions: list[str]) -> list[int]:
    """Indices of every top-level `FROM` instruction, in source order."""
    return [i for i, block in enumerate(instructions) if block.split()[0].upper() == "FROM"]


def _copies_from_builder(tail: list[str]) -> list[tuple[str, str, str]]:
    """Returns `(source, dest, block)` for every `COPY --from=builder ...` instruction in `tail`,
    tolerant of flag order (`--chown=...` before or after `--from=builder`) so the pin doesn't
    over-fit incidental whitespace or flag ordering (mirrors `test_dockerfile_pins.py`'s
    `_runtime_copy_from_builder`).
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


def test_web_builder_is_node_24_slim_with_corepack() -> None:
    """Brief Interfaces: the builder stage is `node:24-slim`, enables corepack (pnpm ships via
    corepack, not a separate install), and runs the frozen-lockfile install before the Next
    build — with the manifest `COPY` preceding the install so dependency resolution is
    layer-cached independently of application source changes.
    """
    instructions = _instructions()
    from_indices = _from_indices(instructions)
    assert from_indices, "no FROM instruction found"

    first_from = instructions[from_indices[0]]
    assert first_from.lower() == "from node:24-slim as builder", (
        f"builder stage is not `FROM node:24-slim AS builder`: {first_from}"
    )

    assert any(
        block.split()[0].upper() == "RUN" and "corepack enable" in block for block in instructions
    ), "no `RUN corepack enable` instruction found"

    install_idx = next(
        (
            i
            for i, block in enumerate(instructions)
            if block.split()[0].upper() == "RUN" and "pnpm install --frozen-lockfile" in block
        ),
        None,
    )
    assert install_idx is not None, "no `RUN pnpm install --frozen-lockfile` instruction found"

    assert any(
        block.split()[0].upper() == "RUN" and "pnpm -C web build" in block for block in instructions
    ), "no `RUN pnpm -C web build` instruction found"

    lockfile_copy_idx = next(
        (
            i
            for i, block in enumerate(instructions)
            if block.split()[0].upper() == "COPY" and "pnpm-lock.yaml" in block
        ),
        None,
    )
    assert lockfile_copy_idx is not None, "no COPY of pnpm-lock.yaml found"
    assert lockfile_copy_idx < install_idx, (
        "the lockfile COPY must precede `RUN pnpm install --frozen-lockfile` (cache the deps layer)"
    )


def test_web_runtime_is_non_root_nextjs_uid_1001() -> None:
    """Brief Interfaces: the runtime stage drops root — a container running as root that is ever
    mis-exposed gives an attacker a much easier escape (mirrors `infra/Dockerfile.api`'s
    `test_runs_as_non_root`).
    """
    instructions = _instructions()
    from_indices = _from_indices(instructions)
    assert len(from_indices) >= 2, "expected a builder stage and a runtime stage"

    last_from = instructions[from_indices[-1]]
    assert last_from.lower() == "from node:24-slim", (
        f"runtime stage is not `FROM node:24-slim`: {last_from}"
    )

    tail = instructions[from_indices[-1] :]
    assert any(block.strip() == "USER nextjs" for block in tail), (
        "no `USER nextjs` instruction after the last FROM"
    )
    assert any("useradd" in block and "1001" in block and "nextjs" in block for block in tail), (
        "no `useradd ... --uid 1001 ... nextjs` instruction after the last FROM"
    )


def test_web_healthcheck_uses_node_fetch_on_healthz() -> None:
    """Brief Interfaces: a stdlib-only Node healthcheck against `/healthz` — no `curl` binary is
    installed or invoked anywhere in the image (mirrors `infra/Dockerfile.api`'s
    `test_healthcheck_present`).
    """
    instructions = _instructions()
    healthchecks = [block for block in instructions if block.split()[0].upper() == "HEALTHCHECK"]
    assert healthchecks, "no HEALTHCHECK instruction found"

    block = healthchecks[0]
    assert "http://127.0.0.1:3000/healthz" in block, (
        f"HEALTHCHECK does not target /healthz: {block}"
    )
    assert '"node"' in block, f"HEALTHCHECK CMD is not the JSON-form `node` invocation: {block}"

    full_text = "\n".join(instructions).lower()
    assert "curl" not in full_text, (
        "curl referenced somewhere in the Dockerfile (stdlib-only healthcheck)"
    )


def test_web_cmd_is_node_standalone_server() -> None:
    """Brief Interfaces / task-03 implementer report: the observed standalone layout nests the
    server at `web/.next/standalone/web/server.js`, so the runtime image's working directory
    (`/app`, holding the copied standalone tree) runs `node web/server.js`. Parsing the
    JSON-array form directly (rather than substring-matching) also rejects a shell-form `CMD`,
    which would swallow signals differently under `docker stop`.
    """
    instructions = _instructions()
    cmd_blocks = [block for block in instructions if block.split()[0].upper() == "CMD"]
    assert cmd_blocks, "no CMD instruction found"

    json_part = cmd_blocks[-1][len("CMD") :].strip()
    parsed = json.loads(json_part)
    assert parsed == ["node", "web/server.js"], f"CMD is not the expected node invocation: {parsed}"


def test_web_copies_standalone_static_and_public() -> None:
    """Brief Interfaces: three `COPY --from=builder` instructions carry the standalone server,
    the built static assets, and the public directory into the runtime stage — Next's standalone
    output does not include `.next/static/` or `public/` on its own (task brief's "Next 16
    standalone facts"). Each copy is `--chown=nextjs:nodejs` so the non-root runtime user can
    read what it serves.
    """
    instructions = _instructions()
    from_indices = _from_indices(instructions)
    tail = instructions[from_indices[-1] :]

    entries = _copies_from_builder(tail)
    required_suffixes = {"/web/.next/standalone", "/web/.next/static", "/web/public"}
    found_suffixes = {
        suffix
        for suffix in required_suffixes
        for source, _dest, _block in entries
        if source.endswith(suffix)
    }
    missing = sorted(required_suffixes - found_suffixes)
    assert not missing, f"runtime stage is missing COPY --from=builder for: {missing}"

    for source, _dest, block in entries:
        if any(source.endswith(suffix) for suffix in required_suffixes):
            assert "--chown=nextjs:nodejs" in block, (
                f"runtime COPY of {source} is missing --chown=nextjs:nodejs: {block}"
            )


def test_web_no_env_file_copied() -> None:
    """CONVENTIONS.md §11 / `.claude/rules/infra.md`: runtime config is env-only. Neither a
    `COPY`/`ADD` of `.env` nor an `ENV` line hardcoding a secret name may ship inside the image
    (mirrors `infra/Dockerfile.api`'s `test_no_env_file_copied`).
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


def test_web_build_args_for_public_and_server_api_url() -> None:
    """Brief Interfaces: `NEXT_PUBLIC_API_URL` is inlined into the client bundle at build time
    (PRD §11 — a new public API origin means a rebuild) and `API_URL` is available to the build
    step too; both `ARG`s must be declared before the `pnpm -C web build` that bakes them in, or
    the build sees the empty default instead of the compose-supplied value.
    """
    instructions = _instructions()

    build_idx = next(
        (
            i
            for i, block in enumerate(instructions)
            if block.split()[0].upper() == "RUN" and "pnpm -C web build" in block
        ),
        None,
    )
    assert build_idx is not None, "no `RUN pnpm -C web build` instruction found"

    arg_names = {
        block.split()[1].split("=")[0]
        for block in instructions[:build_idx]
        if block.split()[0].upper() == "ARG"
    }
    assert "NEXT_PUBLIC_API_URL" in arg_names, "no `ARG NEXT_PUBLIC_API_URL` before the build"
    assert "API_URL" in arg_names, "no `ARG API_URL` before the build"
