"""New (unpinned) file, m6 task-03 fix-1: pins `infra/deploy/push_ecr.sh`'s dirty-tree guard (M5).
`tests/test_deploy_scripts.py` is pinned, so this assertion lives here instead of being added to
that file (the fix brief's ruling — see
`.superpowers/sdd/m6-real-data-deploy/task-03-fix-1-brief.md`).

Pure text/`bash -n` pin, same style as `test_deploy_scripts.py::test_push_ecr_shape` — no live
`git`/`docker`/AWS call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PUSH_ECR = _REPO_ROOT / "infra" / "deploy" / "push_ecr.sh"


def test_push_ecr_refuses_a_dirty_working_tree() -> None:
    """M5: a dirty tree must not produce an image whose SHA tag doesn't describe its contents
    (`docs/deployment.md`'s "a running service can never silently change under a rebuild" promise).
    `push_ecr.sh` checks `git ... status --porcelain` and refuses (rather than silently tagging)
    when it is non-empty.
    """
    assert _PUSH_ECR.exists(), f"{_PUSH_ECR} does not exist yet"
    text = _PUSH_ECR.read_text()

    proc = subprocess.run(
        ["bash", "-n", str(_PUSH_ECR)], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, f"bash -n {_PUSH_ECR} failed:\n{proc.stderr}"

    assert "status --porcelain" in text, text
    assert "dirty" in text, text
