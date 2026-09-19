"""Pins m7 task-07's R48 ruling: `.github/workflows/nightly-eval.yml` gains a
`workflow_dispatch` input `prompt_version` (default `""`) that the run step consumes through a
step/job `env` variable `PROMPT_VERSION_INPUT: ${{ inputs.prompt_version }}` -- never by
interpolating `${{ }}` directly into the shell command (that pattern is an injection lint trap).

Today `workflow_dispatch:` carries no `inputs:` mapping at all (task-05's shape), and the run
step reads `"$TRIAGE_PROMPT_VERSION"` directly with no `PROMPT_VERSION_INPUT` fallback, so this
test fails RED: first on the missing `inputs` key, and even once that's added, the raw-text
assertion on the run step still fails until the shell fallback (`PROMPT="${PROMPT_VERSION_INPUT:-
$TRIAGE_PROMPT_VERSION}"`) lands.

Matches `tests/test_nightly_workflow_pins.py`'s YAML-parsing approach: PyYAML's `SafeLoader`
resolves the bare `on:` key to the Python bool `True` (YAML 1.1 implicit typing), so the
triggers block is read via `_on_block` exactly like the sibling pin file does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "nightly-eval.yml"


def _on_block(workflow: dict[Any, Any]) -> dict[str, Any]:
    """PyYAML resolves the bare `on:` key to the Python bool `True` (YAML 1.1 implicit typing);
    read it under either key so a future quoted `"on":` keeps working too."""
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def _find_gate_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in steps:
        run = str(step.get("run", ""))
        if "evals.run" in run and "--gate" in run:
            return step
    return None


def test_nightly_workflow_has_prompt_version_dispatch_input() -> None:
    raw_text = _WORKFLOW_PATH.read_text()
    workflow = yaml.safe_load(raw_text)

    # --- parsed structure: workflow_dispatch declares a prompt_version input, default "" --------
    on_block = _on_block(workflow)
    dispatch = on_block.get("workflow_dispatch")
    assert isinstance(dispatch, dict), (
        "workflow_dispatch must be a mapping with an `inputs` block, not a bare/null trigger"
    )

    inputs = dispatch.get("inputs")
    assert isinstance(inputs, dict), "workflow_dispatch.inputs must be a mapping"
    assert "prompt_version" in inputs, "workflow_dispatch.inputs.prompt_version is missing"

    prompt_version_input = inputs["prompt_version"]
    assert isinstance(prompt_version_input, dict)
    assert prompt_version_input.get("default") == "", (
        f"prompt_version's default must be the empty string, got "
        f"{prompt_version_input.get('default')!r}"
    )
    if "required" in prompt_version_input:
        assert prompt_version_input["required"] is False

    # --- wiring: the gate run step references PROMPT_VERSION_INPUT, not `${{ }}` interpolation ---
    job = workflow["jobs"]["nightly-eval"]
    steps = job["steps"]
    gate_step = _find_gate_step(steps)
    assert gate_step is not None, "no run step invoking `evals.run ... --gate` was found"

    step_env = gate_step.get("env") or {}
    job_env = job.get("env") or {}
    merged_env = {**job_env, **step_env}
    assert "PROMPT_VERSION_INPUT" in merged_env, (
        "PROMPT_VERSION_INPUT must be set via the step (or job) env, not interpolated with "
        "${{ }} directly into the shell command"
    )
    assert "${{ inputs.prompt_version }}" in str(merged_env["PROMPT_VERSION_INPUT"]), (
        "PROMPT_VERSION_INPUT must be bound to the ${{ inputs.prompt_version }} expression"
    )

    run_command = str(gate_step.get("run", ""))
    assert "PROMPT_VERSION_INPUT" in run_command, (
        "the run command must reference PROMPT_VERSION_INPUT (e.g. via a shell fallback "
        '`PROMPT="${PROMPT_VERSION_INPUT:-$TRIAGE_PROMPT_VERSION}"`), not read '
        "$TRIAGE_PROMPT_VERSION unconditionally"
    )

    # --- raw-text check: pins the real wiring, not just the parsed structure ---------------------
    assert "prompt_version" in raw_text
    assert "PROMPT_VERSION_INPUT" in raw_text
    assert "${{ inputs.prompt_version }}" in raw_text
