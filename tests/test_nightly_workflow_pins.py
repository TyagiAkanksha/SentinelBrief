"""Pins `.github/workflows/nightly-eval.yml`'s shape against the m7 task-05 brief's Interfaces
block and "Controller rulings" section: runs at `23 3 * * *` UTC plus `workflow_dispatch`, needs
NO postgres/redis services (evals are DB-less), replays the strict-by-default v2 harness with
`--gate`, uploads the per-run JSON `if: always()`, never `continue-on-error`s past a gate
failure, and pins its non-secret model config to `infra/deploy/prod/docker-compose.yml`'s
`x-shared-env` anchor -- not just `TRIAGE_PROMPT_VERSION`, every one of the five shared values
(ruling: "the pin test reads both files and asserts equality for all five").

`.github/workflows/nightly-eval.yml` does not exist yet, so the file-read below fails RED today
with `FileNotFoundError`.

`pyyaml` is a confirmed runtime dependency here (`worker.tools.asset_info` reads YAML;
`tests/test_honeypot_compose.py` already parses a compose file with `yaml.safe_load` the same
way), so this module imports it directly rather than falling back to text-substring pins.

PyYAML's `SafeLoader` resolves YAML 1.1's implicit boolean words -- the bare `on:` key among
them -- so a parsed workflow's schedule/dispatch triggers live under the Python key `True`, not
the string `"on"`; `_on_block` reads it either way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "nightly-eval.yml"
_PROD_COMPOSE_PATH = _REPO_ROOT / "infra" / "deploy" / "prod" / "docker-compose.yml"

_SHARED_ENV_KEYS = (
    "LLM_BASE_URL",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
)

_GATE_RUN_COMMAND = (
    'uv run python -m evals.run --golden evals/golden/v2.jsonl --prompt "$TRIAGE_PROMPT_VERSION" '
    '--strong-model "$STRONG_MODEL" --gate --output-dir evals/results'
)


def _on_block(workflow: dict[Any, Any]) -> dict[str, Any]:
    """PyYAML resolves the bare `on:` key to the Python bool `True` (YAML 1.1 implicit typing);
    read it under either key so a future quoted `"on":` keeps working too."""
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def _collect_env(job: dict[str, Any]) -> dict[str, str]:
    """Every `env:` mapping visible to the job's steps, merged job-level-first then step-level,
    regardless of whether the implementer put the five shared values at the job or step level."""
    merged: dict[str, str] = {}
    merged.update(job.get("env") or {})
    for step in job.get("steps", []):
        merged.update(step.get("env") or {})
    return merged


def _find_step(
    steps: list[dict[str, Any]], *, uses_prefix: str | None = None, run_substring: str | None = None
) -> dict[str, Any] | None:
    for step in steps:
        if uses_prefix is not None and str(step.get("uses", "")).startswith(uses_prefix):
            return step
        if run_substring is not None and run_substring in str(step.get("run", "")):
            return step
    return None


def test_nightly_workflow_shape() -> None:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    compose = yaml.safe_load(_PROD_COMPOSE_PATH.read_text())
    shared_env = compose["x-shared-env"]

    # --- triggers: 03:23 UTC nightly cron, plus a manual dispatch escape hatch -------------------
    on_block = _on_block(workflow)
    assert on_block.get("schedule") == [{"cron": "23 3 * * *"}]
    assert "workflow_dispatch" in on_block

    # --- exactly one job, no postgres/redis services (evals are DB-less) --------------------------
    jobs = workflow["jobs"]
    assert "nightly-eval" in jobs
    job = jobs["nightly-eval"]
    assert not job.get("services")

    steps = job["steps"]

    checkout = _find_step(steps, uses_prefix="actions/checkout@v7")
    assert checkout is not None

    setup_uv = _find_step(steps, uses_prefix="astral-sh/setup-uv@v10.0.1")
    assert setup_uv is not None
    assert setup_uv.get("with", {}).get("python-version") == "3.12"

    # UV_LOCKED: "1", exactly like ci.yml's python job.
    workflow_env = {**(workflow.get("env") or {}), **(job.get("env") or {})}
    assert str(workflow_env.get("UV_LOCKED")) == "1"

    # --- the run step: strict-replay harness against the active prompt, gated ------------------
    gate_step = _find_step(steps, run_substring=_GATE_RUN_COMMAND)
    assert gate_step is not None

    # --- the five non-secret model-config values pinned to the prod compose's x-shared-env --------
    merged_env = _collect_env(job)
    for key in _SHARED_ENV_KEYS:
        assert key in merged_env, f"{key} is not set as a workflow env value"
        assert merged_env[key] == shared_env[key], (
            f"{key} ({merged_env[key]!r}) does not match infra/deploy/prod/docker-compose.yml's "
            f"x-shared-env value ({shared_env[key]!r})"
        )

    # LLM_API_KEY comes from the repository secret, never a literal value.
    assert "secrets.LLM_API_KEY" in str(merged_env.get("LLM_API_KEY", ""))

    # --- the per-run JSON artifact is uploaded on every outcome, including a gate failure ---------
    upload_step = _find_step(steps, uses_prefix="actions/upload-artifact")
    assert upload_step is not None
    assert str(upload_step.get("if", "")).strip() == "always()"

    # --- nothing in this job may swallow a failure: a gate trip must fail the whole run -----------
    assert all("continue-on-error" not in step for step in steps)
