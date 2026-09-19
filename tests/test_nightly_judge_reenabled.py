"""Pins the removal of M7's `--no-judge` flag from `.github/workflows/nightly-eval.yml`'s gate
step (m8b task-05 "Folds these deferred findings": the daily token-budget breaker now caps LLM
spend, so the nightly gate can judge again). Duplicated file-read/step-lookup logic from
`tests/test_nightly_workflow_pins.py` per the repo's own convention (test files never import from
each other) -- that file's own `_GATE_RUN_COMMAND` substring pin does not name `--no-judge`
either way (a substring match stays green whether or not the flag trails it), so this is the one
file that actually pins the flag's *absence*.

Today the flag is still present (M7's own addition, with a comment explaining why) -- RED.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "nightly-eval.yml"
)


def test_no_judge_flag_is_not_in_the_nightly_gate_run_step() -> None:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    job = workflow["jobs"]["nightly-eval"]
    run_commands = [str(step.get("run", "")) for step in job["steps"]]

    assert any("evals.run" in cmd for cmd in run_commands), (
        "expected the nightly gate step (running evals.run) to exist"
    )
    assert not any("--no-judge" in cmd for cmd in run_commands), (
        "the nightly gate must judge again now that the daily token-budget breaker caps spend "
        "(m8b task-05 reverts M7's --no-judge)"
    )
