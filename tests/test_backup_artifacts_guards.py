"""New (unpinned) file, m6 final-review fix wave: durable guards for the backup/restore details
that `tests/test_backup_artifacts.py` (pinned) leaves unpinned — the deferred Minors the final
review re-graded FIX-NOW (`.superpowers/sdd/m6-real-data-deploy/m6-final-review.md`, roster items
t04 N1, N2, N4 and new M1).

`tests/test_backup_artifacts.py` is pinned (sha256 in the task-04 test-author's report), so these
assertions live here rather than being added to it — adding a new test file is always allowed.
The one approved amendment to that pinned file (ruling R16, the `(recorded during deployment)`
marker) is unrelated to anything below.

Pure text/`bash -n` pins — no docker, no live AWS, no systemd, no S3.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_BACKUP_SH = _REPO_ROOT / "infra" / "deploy" / "prod" / "backup.sh"
_BACKUP_SERVICE = _REPO_ROOT / "infra" / "deploy" / "prod" / "sentinelbrief-backup.service"
_DATABASE_MD = _REPO_ROOT / "infra" / "deploy" / "database.md"
_DEPLOYMENT_DOC = _REPO_ROOT / "docs" / "deployment.md"


def _dr_fence(text: str) -> str:
    """The disaster-recovery fenced block in `database.md` — the one that drops and recreates the
    LIVE database. Identified by its `DUMP=` first line so a second, unrelated fence can never be
    mistaken for it."""
    fences = re.findall(r"```sh\n(.*?)```", text, re.DOTALL)
    matching = [f for f in fences if f.startswith("DUMP=")]
    assert len(matching) == 1, (
        f"expected exactly one `DUMP=`-rooted DR fence in database.md, found {len(matching)}"
    )
    return matching[0]


def test_backup_script_cleans_up_its_partial_dump() -> None:
    """Final review M1: a failing `pg_dump` (or `gzip`) aborts under `set -euo pipefail` before
    the `mv`, leaving `$OUT.part` on disk every night it fails. `.part` never matches the
    `sentinelbrief-*.sql.gz` prune glob, so the debris accumulates unbounded on a 20 GB root
    volume. An `EXIT` trap removes it on every exit path; on the success path the file has already
    been renamed, so the `rm -f` is a harmless no-op.

    Also re-pins the write-then-verify-then-rename ORDER that makes the dump atomic (t04 N1): the
    dump is written to `$OUT.part`, `test -s` rejects an empty dump, and only then does the `mv`
    make it match the glob the prune step and the S3 upload both use. `tests/
    test_backup_artifacts.py::test_backup_script_shape` pins that `test -s` appears somewhere in
    the file, not that it appears BETWEEN the two.
    """
    assert _BACKUP_SH.exists(), f"{_BACKUP_SH} does not exist"
    text = _BACKUP_SH.read_text()

    proc = subprocess.run(
        ["bash", "-n", str(_BACKUP_SH)], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, f"bash -n {_BACKUP_SH} failed:\n{proc.stderr}"

    assert re.search(r"^trap\s+'rm -f \"\$OUT\.part\"'\s+EXIT", text, re.MULTILINE), (
        "backup.sh has no `trap 'rm -f \"$OUT.part\"' EXIT` — a failed dump leaves its .part file "
        "behind, and .part never matches the prune glob (final review M1)"
    )

    write_part = '> "$OUT.part"'
    verify = 'test -s "$OUT.part"'
    rename = 'mv "$OUT.part" "$OUT"'
    for needle in (write_part, verify, rename):
        assert needle in text, f"backup.sh missing required text: {needle!r}"
    assert text.index(write_part) < text.index(verify) < text.index(rename), (
        "backup.sh must write to $OUT.part, reject an empty dump with `test -s`, and only THEN "
        "`mv` it onto the name the prune glob and the S3 upload use (t04 N1)"
    )


def test_backup_service_bounds_its_own_runtime() -> None:
    """t04 N1: a `oneshot` unit with no `TimeoutStartSec=` inherits systemd's default
    `DefaultTimeoutStartSec` (90 s on AL2023), which a growing `pg_dump` will eventually exceed —
    systemd would then kill a healthy backup mid-upload every night. The unit pins an explicit,
    generous bound instead. `tests/test_backup_artifacts.py::test_timer_and_service_shape` pins
    `Type=oneshot`/`ExecStart`/`Requires` but not this line.
    """
    assert _BACKUP_SERVICE.exists(), f"{_BACKUP_SERVICE} does not exist"
    text = _BACKUP_SERVICE.read_text()
    assert re.search(r"^TimeoutStartSec=\S+", text, re.MULTILINE), (
        "sentinelbrief-backup.service has no explicit TimeoutStartSec= (t04 N1)"
    )


def test_disaster_recovery_block_is_chained_and_verifies_first() -> None:
    """t04 N1 + N4: `database.md`'s DR block is pasted into a root shell under stress, and it
    drops the live database. Every step must be `&&`-chained off the one before it, so a failed
    integrity check can never be followed by a successful `DROP DATABASE` — the worst outcome
    available here. The chain must also: name the dump once (`DUMP=`, so the four references
    cannot drift apart), verify the archive with `gunzip -t` BEFORE anything touches the live
    database, stop `api`/`worker` before the drop, use `WITH (FORCE)` so the healthcheck's own
    connection cannot block the drop, and restore with `ON_ERROR_STOP=1` so a partial restore
    fails loudly instead of silently succeeding.
    """
    assert _DATABASE_MD.exists(), f"{_DATABASE_MD} does not exist"
    fence = _dr_fence(_DATABASE_MD.read_text())

    for needle in (
        "gunzip -t",
        "stop api worker",
        "DROP DATABASE sentinelbrief WITH (FORCE)",
        "ON_ERROR_STOP=1",
        "start api worker",
    ):
        assert needle in fence, f"database.md's DR block missing: {needle!r}\n{fence}"

    assert (
        fence.index("gunzip -t")
        < fence.index("stop api worker")
        < fence.index("DROP DATABASE sentinelbrief WITH (FORCE)")
        < fence.index("ON_ERROR_STOP=1")
    ), "database.md's DR block runs its steps out of order — verify, stop, drop, restore:\n" + fence

    steps = [line for line in fence.splitlines() if line.strip() and not line.startswith("DUMP=")]
    unchained = [
        line
        for line in steps[:-1]
        if not line.rstrip().endswith("\\") and not line.rstrip().endswith("&&")
    ]
    assert not unchained, (
        "every step of database.md's DR block must be `&&`-chained to the next so a failure stops "
        "the sequence (t04 N4); these lines are not:\n" + "\n".join(unchained)
    )


def test_deployment_doc_never_calls_the_backup_a_cron_job() -> None:
    """t04 N2: PRD v1.5 replaced "a cron container" with a host systemd timer everywhere, but the
    doc of record's own architecture diagram kept a stale `backup (cron)` box that no test saw —
    `tests/test_backup_artifacts.py::test_prd_and_deployment_doc_say_systemd_timer` only forbids
    the exact phrase "cron container". Nothing in this deployment runs from cron, so the word has
    no legitimate use in the file.
    """
    assert _DEPLOYMENT_DOC.exists(), f"{_DEPLOYMENT_DOC} does not exist"
    text = _DEPLOYMENT_DOC.read_text()
    assert "cron" not in text, (
        "docs/deployment.md still says 'cron' — backups run from sentinelbrief-backup.timer, a "
        "host systemd timer (PRD v1.5, t04 N2)"
    )
