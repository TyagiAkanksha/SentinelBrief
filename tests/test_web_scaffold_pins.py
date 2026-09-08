"""Pins the `web/` scaffold's package.json script roster + version pins, the CI `web` job's
codegen-drift step, and the committed generated types against m3 task-03's Interfaces block
(docs/plans/m3-read-path-dashboard/task-03-web-scaffold-codegen-ci.md): every script is a gate
(FRONTEND-CONVENTIONS.md §1), every dependency is an exact pin (no `^`/`~`), CI re-runs codegen
and fails on drift (CONVENTIONS.md §8), and the committed `schema.d.ts` actually reflects the
task-02 `api/openapi.json` baseline it was generated from.

These are pure text/JSON pins over already-committed (or about-to-be-committed) files -- no
`pnpm` invocation, no network, no DB. `web/` does not exist yet at RED time, so
`test_web_package_exposes_every_gate_script`, `test_web_package_pins_exact_versions` and
`test_generated_types_reference_read_operation_ids` fail on `FileNotFoundError`;
`test_ci_has_web_job_with_codegen_drift_step` fails on a plain assertion because `ci.yml`'s
`python` job already exists (task-02) but its `web` job does not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_JSON = _REPO_ROOT / "web" / "package.json"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_GENERATED_SCHEMA = _REPO_ROOT / "web" / "src" / "types" / "generated" / "schema.d.ts"

_EXACT_PIN = re.compile(r"^\d+\.\d+\.\d+$")

# FRONTEND-CONVENTIONS.md §1: "every script is a gate" -- the exact eleven names task-03's
# Interfaces block requires, each command byte-for-byte as specified, nothing more, nothing less.
_EXPECTED_SCRIPTS = {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint .",
    "lint:fix": "eslint . --fix",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "type-check": "next typegen && tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest",
    "codegen": "openapi-typescript ../api/openapi.json -o src/types/generated/schema.d.ts",
}


def _load_package_json() -> dict[str, object]:
    data = json.loads(_PACKAGE_JSON.read_text())
    assert isinstance(data, dict)
    return data


def test_web_package_exposes_every_gate_script() -> None:
    """task-03 Interfaces: `web/package.json`'s `scripts` map is exactly the eleven
    FRONTEND-CONVENTIONS §1 gate names, each command byte-for-byte as specified -- an implementer
    who renames or reshuffles a script silently breaks the CI `web` job's `pnpm -C web <name>`
    invocations (see `test_ci_has_web_job_with_codegen_drift_step` below) with no other signal.
    """
    data = _load_package_json()
    scripts = data["scripts"]
    assert isinstance(scripts, dict)
    assert set(scripts.keys()) == set(_EXPECTED_SCRIPTS.keys()), (
        f"script roster drifted from the FRONTEND-CONVENTIONS §1 gate set: {sorted(scripts)}"
    )
    for name, expected_command in _EXPECTED_SCRIPTS.items():
        assert scripts[name] == expected_command, (
            f"scripts.{name} = {scripts[name]!r}, expected {expected_command!r}"
        )
    assert "../api/openapi.json" in scripts["codegen"], (
        "codegen script does not read the task-02 baseline at ../api/openapi.json"
    )


def test_web_package_pins_exact_versions() -> None:
    """task-03 Interfaces: every `dependencies`/`devDependencies` entry is an exact `x.y.z` pin
    (no `^`/`~` range) -- a caret pin would let `pnpm install` silently drift the toolchain
    between CI runs, defeating the point of recording exact versions in the brief. `next` must be
    a 16.x release (the Next 16 App Router facts the rest of the scaffold is built against).
    """
    data = _load_package_json()
    dependencies = data.get("dependencies", {})
    dev_dependencies = data.get("devDependencies", {})
    assert isinstance(dependencies, dict) and dependencies, "web/package.json has no dependencies"
    assert isinstance(dev_dependencies, dict) and dev_dependencies, (
        "web/package.json has no devDependencies"
    )

    for scope_name, scope in (
        ("dependencies", dependencies),
        ("devDependencies", dev_dependencies),
    ):
        for pkg_name, version in scope.items():
            assert isinstance(version, str) and _EXACT_PIN.fullmatch(version), (
                f"{scope_name}.{pkg_name} = {version!r} is not an exact x.y.z pin"
            )

    next_version = dependencies["next"]
    assert isinstance(next_version, str) and next_version.startswith("16."), (
        f"next is pinned to {next_version!r}, expected a 16.x release"
    )


def test_ci_has_web_job_with_codegen_drift_step() -> None:
    """task-03 Interfaces: `.github/workflows/ci.yml` gains a second `web:` job that re-runs
    codegen and fails on drift and runs the four gate scripts, alongside the existing task-02
    `export_openapi.py` drift step that guards the DTO<->OpenAPI baseline the `web` job's own
    codegen step consumes (CONVENTIONS.md §8: both baselines are drift-checked in the same CI
    run).
    """
    text = _CI_WORKFLOW.read_text()
    assert "\n  web:\n" in text, "ci.yml has no top-level `web:` job"
    assert "pnpm -C web codegen && git diff --exit-code -- web/src/types/generated" in text, (
        "ci.yml web job is missing the codegen-drift step"
    )
    for gate_command in (
        "pnpm -C web lint",
        "pnpm -C web type-check",
        "pnpm -C web format:check",
        "pnpm -C web test",
    ):
        assert gate_command in text, f"ci.yml web job is missing `{gate_command}`"
    assert "export_openapi.py --out /tmp/openapi.json && cmp" in text, (
        "ci.yml lost task-02's OpenAPI baseline drift step"
    )


def test_generated_types_reference_read_operation_ids() -> None:
    """task-03 Interfaces: `src/types/generated/schema.d.ts` is `openapi-typescript` run over
    the committed `api/openapi.json` baseline, committed as-is -- if it were hand-edited or
    generated against a stale baseline, the read-path operation ids and schema names task-04+
    depends on (`web/src/types/api.ts` aliases over exactly these names) would silently
    disappear.
    """
    text = _GENERATED_SCHEMA.read_text()
    for needle in (
        "list_alerts",
        "get_alert",
        "get_stats",
        "PaginatedResponse_AlertSummary_",
        "ErrorEnvelope",
    ):
        assert needle in text, f"generated schema.d.ts is missing {needle!r}"
