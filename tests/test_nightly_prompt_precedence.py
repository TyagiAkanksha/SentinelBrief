"""Fix round 1 (I2) for m7 task-07: `tests/test_nightly_dispatch_input.py` only checks that
`PROMPT_VERSION_INPUT` APPEARS in the gate run step's command, so it stays green even if the
fallback precedence were inverted to `${TRIAGE_PROMPT_VERSION:-$PROMPT_VERSION_INPUT}` -- which
would silently ignore the `workflow_dispatch` input, the exact opposite of ruling R48. This file
pins the correct bash parameter-expansion precedence directly: the dispatch input is primary, the
env default is the `:-` fallback.

Matches the sibling pin files' YAML-parsing approach (PyYAML resolves the bare `on:` key to the
Python bool `True` under YAML 1.1 implicit typing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "nightly-eval.yml"

_CORRECT_PRECEDENCE = "${PROMPT_VERSION_INPUT:-$TRIAGE_PROMPT_VERSION}"
_INVERTED_PRECEDENCE = "${TRIAGE_PROMPT_VERSION:-$PROMPT_VERSION_INPUT}"


def _find_gate_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in steps:
        run = str(step.get("run", ""))
        if "evals.run" in run and "--gate" in run:
            return step
    return None


def test_run_step_prefers_dispatch_input_over_env_default() -> None:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    job = workflow["jobs"]["nightly-eval"]
    steps = job["steps"]
    gate_step = _find_gate_step(steps)
    assert gate_step is not None, "no run step invoking `evals.run ... --gate` was found"

    run_command = str(gate_step.get("run", ""))
    assert _CORRECT_PRECEDENCE in run_command, (
        "the gate run step must fall back to TRIAGE_PROMPT_VERSION only when the dispatch input "
        f"is unset (expected the exact substring {_CORRECT_PRECEDENCE!r})"
    )
    assert _INVERTED_PRECEDENCE not in run_command, (
        "the dispatch input must be primary; the inverted precedence "
        f"{_INVERTED_PRECEDENCE!r} would silently ignore workflow_dispatch's prompt_version"
    )
