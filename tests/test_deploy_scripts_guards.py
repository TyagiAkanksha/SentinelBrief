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


_FETCH_SECRETS = _REPO_ROOT / "infra" / "deploy" / "prod" / "fetch-secrets.sh"


def test_fetch_secrets_renders_both_files_or_neither() -> None:
    """M6 final review, task-03 review N1: the two rendered files carry the SAME password —
    the app file's `DATABASE_URL` embeds the postgres file's `POSTGRES_PASSWORD`. The original
    script moved the app file into place BEFORE fetching `POSTGRES_PASSWORD`, so a transient SSM
    failure on that last fetch left a half-rendered PAIR: a new app secret set against the old
    postgres password, which is worse than leaving both old. Both `mv`s must therefore come after
    every required fetch. The final review's mutation M9 (restoring the old order) survived every
    test on the branch, which is why this pin exists.
    """
    assert _FETCH_SECRETS.exists(), f"{_FETCH_SECRETS} does not exist"
    text = _FETCH_SECRETS.read_text()

    proc = subprocess.run(
        ["bash", "-n", str(_FETCH_SECRETS)], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, f"bash -n {_FETCH_SECRETS} failed:\n{proc.stderr}"

    app_mv = 'mv "$OUT" "$OUT_DEST"'
    pg_fetch_loop = "for P in POSTGRES_PASSWORD"
    pg_mv = 'mv "$OUT_PG" "$OUT_PG_DEST"'
    for needle in (app_mv, pg_fetch_loop, pg_mv):
        assert needle in text, f"fetch-secrets.sh no longer contains {needle!r}"

    assert text.index(app_mv) > text.index(pg_fetch_loop), (
        "fetch-secrets.sh moves the app secrets file into place BEFORE fetching "
        "POSTGRES_PASSWORD — a failure there leaves a half-rendered pair (review N1)"
    )
    assert text.index(pg_mv) > text.index(pg_fetch_loop), (
        "fetch-secrets.sh moves the postgres secrets file into place before its own fetch"
    )
