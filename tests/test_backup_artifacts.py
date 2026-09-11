"""Pins the m6 task-04 backup artifacts against the brief's Interfaces block: the nightly
`backup.sh` script (pg_dump | gzip -> S3, keeps the 3 newest locally, never leaks a raw path or a
credential), `backup.env` (exactly one uncommented, non-secret bucket assignment),
`sentinelbrief-backup.{service,timer}` (root systemd unit shapes), `s3-lifecycle.json` (the 30-day
expiration rule), `restore-rehearsal.sh` (restores into a scratch database and never touches the
live `sentinelbrief` database), `infra/deploy/database.md` (Backups/Restore/Retention-decision
sections), and the PRD + doc-of-record wording change from "a cron container" to a host systemd
timer (PRD.md v1.5; `docs/deployment.md`'s "Data" section).

Pure text/JSON/`bash -n` pins, no docker, no live AWS/systemd. `bash -n` (syntax-only parse, no
execution) is the same pattern `tests/test_deploy_scripts.py` uses for `push_ecr.sh` /
`fetch-secrets.sh` — copied here, not imported, per the brief's Context note. Unit files
(`.service`/`.timer`) are plain `KEY=VALUE`-per-line INI text; a bare substring/line pin is enough
to catch a wrong calendar spec, a missing `Persistent=true`, or a service that isn't `oneshot` —
`configparser` would collapse systemd's duplicate-key-friendly, section-repeating format for no
benefit here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_BACKUP_SH = _REPO_ROOT / "infra" / "deploy" / "prod" / "backup.sh"
_BACKUP_ENV = _REPO_ROOT / "infra" / "deploy" / "prod" / "backup.env"
_BACKUP_SERVICE = _REPO_ROOT / "infra" / "deploy" / "prod" / "sentinelbrief-backup.service"
_BACKUP_TIMER = _REPO_ROOT / "infra" / "deploy" / "prod" / "sentinelbrief-backup.timer"
_S3_LIFECYCLE = _REPO_ROOT / "infra" / "deploy" / "s3-lifecycle.json"
_RESTORE_REHEARSAL = _REPO_ROOT / "infra" / "deploy" / "prod" / "restore-rehearsal.sh"
_DATABASE_MD = _REPO_ROOT / "infra" / "deploy" / "database.md"
_PRD = _REPO_ROOT / "PRD.md"
_DEPLOYMENT_DOC = _REPO_ROOT / "docs" / "deployment.md"

_LIVE_DB_TARGET_RE = re.compile(r"-d sentinelbrief(\s|$)", re.MULTILINE)
_THIRTY_DAY_RE = re.compile(r"30[- ]days?", re.IGNORECASE)
_ECHO_LINE_RE = re.compile(r"(^|\s)echo(\s|$)")


def _bash_dash_n(path: Path) -> subprocess.CompletedProcess[str]:
    """Syntax-checks a shell script without executing it (copied from
    `tests/test_deploy_scripts.py::_bash_dash_n`, not imported — task-04 brief's Context note)."""
    return subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10)


def _require(path: Path) -> str:
    assert path.exists(), (
        f"{path} does not exist yet — task-04's GREEN step creates it per the brief's Interfaces "
        "block."
    )
    return path.read_text()


def _heading_present(text: str, heading_word: str) -> bool:
    """True if `heading_word` appears on a markdown heading line (any level `#`-`######`)."""
    return bool(re.search(rf"^#{{1,6}}\s+.*{re.escape(heading_word)}", text, re.MULTILINE))


def _section(text: str, start_heading: str, end_heading: str | None) -> str:
    """Slices `text` from `start_heading` (inclusive) up to `end_heading` (exclusive), or to the
    end of the file when `end_heading` is `None`. Both headings are matched as literal substrings.
    """
    start = text.index(start_heading)
    if end_heading is None:
        return text[start:]
    end = text.index(end_heading, start)
    return text[start:end]


def test_backup_script_shape() -> None:
    """Interfaces row `backup.sh` / `::test_backup_script_shape`: valid bash; root-executable;
    dumps the single `sentinelbrief` database (never `pg_dumpall`) as plain SQL via the compose
    `postgres` service, never passing a password on the command line or via `PGPASSWORD=` (the
    instance role's S3 permission is the only credential in play); gzips, uploads with `aws s3
    cp`, fails loudly on an empty dump (`test -s`), and prunes local copies down to the newest 3
    (`tail -n +4`); the only `echo` line is the operator-facing summary and never interpolates a
    raw path or dump content — it always starts with `backup ok key=`.
    """
    text = _require(_BACKUP_SH)

    proc = _bash_dash_n(_BACKUP_SH)
    assert proc.returncode == 0, f"bash -n {_BACKUP_SH} failed:\n{proc.stderr}"

    assert os.access(_BACKUP_SH, os.X_OK), f"{_BACKUP_SH} is not executable"

    for needle in (
        "set -euo pipefail",
        "pg_dump -U sentinel -d sentinelbrief",
        "--format=plain",
        "gzip",
        "aws s3 cp",
        "test -s",
        "tail -n +4",
    ):
        assert needle in text, f"backup.sh missing required text: {needle!r}"

    for forbidden in ("pg_dumpall", "--password", "PGPASSWORD="):
        assert forbidden not in text, f"backup.sh must not contain: {forbidden!r}"

    echo_lines = [
        line
        for line in text.splitlines()
        if _ECHO_LINE_RE.search(line) and not line.strip().startswith("#")
    ]
    assert echo_lines, "backup.sh has no echo line"
    assert len(echo_lines) == 1, (
        f"expected exactly one echo line, found {len(echo_lines)}: {echo_lines}"
    )
    assert "backup ok key=" in echo_lines[0], (
        f"the only echo line must print 'backup ok key=': {echo_lines[0]!r}"
    )


def test_backup_env_has_only_the_bucket() -> None:
    """Interfaces row `backup.env` / `::test_backup_env_has_only_the_bucket`: the file carries
    exactly one uncommented assignment — the bucket name — because a bucket name is
    instance-specific but not a secret (`.claude/rules/infra.md`); everything else in the file is
    a `#`-prefixed comment.
    """
    text = _require(_BACKUP_ENV)

    uncommented = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    assert len(uncommented) == 1, (
        f"expected exactly one uncommented assignment in backup.env, found {len(uncommented)}: "
        f"{uncommented}"
    )
    assert uncommented[0].startswith("BACKUP_S3_BUCKET=sentinelbrief-backups-"), uncommented[0]


def test_timer_and_service_shape() -> None:
    """Interfaces row `unit files` / `::test_timer_and_service_shape`: the timer runs nightly at
    03:15 UTC, catches up a missed run at boot (`Persistent=true`), and is installed under
    `timers.target`; the service is a `oneshot` that runs `backup.sh` and refuses to start before
    Docker is up (`Requires=docker.service`) — a systemd ordering/persistence mutant (wrong
    calendar spec, `Persistent=false`, a long-running `Type=`) is what this test catches.
    """
    timer_text = _require(_BACKUP_TIMER)
    service_text = _require(_BACKUP_SERVICE)

    for needle in ("OnCalendar=*-*-* 03:15:00 UTC", "Persistent=true", "WantedBy=timers.target"):
        assert needle in timer_text, f"sentinelbrief-backup.timer missing: {needle!r}"

    for needle in (
        "Type=oneshot",
        "ExecStart=/opt/sentinelbrief/backup.sh",
        "Requires=docker.service",
    ):
        assert needle in service_text, f"sentinelbrief-backup.service missing: {needle!r}"


def test_s3_lifecycle_expires_dumps_after_30_days() -> None:
    """Interfaces row `lifecycle` / `::test_s3_lifecycle_expires_dumps_after_30_days`: the bucket
    lifecycle rule is enabled, scoped to the `postgres/` prefix only (never the whole bucket), and
    expires objects after exactly 30 days — the number PRD §11 and `database.md`'s retention
    decision both name.
    """
    text = _require(_S3_LIFECYCLE)

    data = json.loads(text)
    rule = data["Rules"][0]
    assert rule["Status"] == "Enabled", rule
    assert rule["Filter"]["Prefix"] == "postgres/", rule
    assert rule["Expiration"]["Days"] == 30, rule


def test_restore_rehearsal_never_touches_the_live_db() -> None:
    """Interfaces row `rehearsal` / `::test_restore_rehearsal_never_touches_the_live_db`: valid
    bash; restores into a disposable `sentinelbrief_restore_check` database with
    `ON_ERROR_STOP=1` so a partial restore fails loudly instead of silently succeeding; verifies
    `alembic_version` migrated along with the data; and — the load-bearing pin — never targets the
    live `sentinelbrief` database as a `-d` argument (only `_restore_check` or `postgres` are
    legal `-d` targets) and never contains the literal `DROP DATABASE sentinelbrief;` that would
    destroy production data instead of the scratch copy.
    """
    text = _require(_RESTORE_REHEARSAL)

    proc = _bash_dash_n(_RESTORE_REHEARSAL)
    assert proc.returncode == 0, f"bash -n {_RESTORE_REHEARSAL} failed:\n{proc.stderr}"

    for needle in (
        "sentinelbrief_restore_check",
        "ON_ERROR_STOP=1",
        "DROP DATABASE IF EXISTS sentinelbrief_restore_check",
        "alembic_version",
    ):
        assert needle in text, f"restore-rehearsal.sh missing: {needle!r}"

    live_db_hits = _LIVE_DB_TARGET_RE.findall(text)
    assert not live_db_hits, (
        f"restore-rehearsal.sh targets the live 'sentinelbrief' database as a -d argument: "
        f"{live_db_hits}"
    )
    assert "DROP DATABASE sentinelbrief;" not in text, (
        "restore-rehearsal.sh must never drop the live 'sentinelbrief' database"
    )


def test_database_doc_sections() -> None:
    """Interfaces row `database.md` / `::test_database_doc_sections`: the restore procedure and
    the retention decision exist as named sections before any real attacker data lands
    (`docs/deployment.md` "Data: backups and retention"); the doc names the rehearsal script, the
    30-day lifecycle, the named Postgres volume, and marks the soak-time measurement row as a
    placeholder task-06 fills in (`(recorded during deployment)`).
    """
    text = _require(_DATABASE_MD)

    for heading in ("Backups", "Restore", "Retention decision"):
        assert _heading_present(text, heading), f"database.md has no heading containing {heading!r}"

    assert "restore-rehearsal.sh" in text, text
    assert _THIRTY_DAY_RE.search(text), "database.md has no '30-day'/'30 days' sentence"
    assert "sentinelbrief_pg" in text, text
    assert "(recorded during deployment)" in text, text


def test_prd_and_deployment_doc_say_systemd_timer() -> None:
    """Interfaces row `PRD + doc of record` / `::test_prd_and_deployment_doc_say_systemd_timer`:
    the briefing's ruling (PRD §11 amended v1.5) replaces "a cron container" with a host systemd
    timer everywhere it is named — PRD §11 says "systemd timer"; `docs/deployment.md`'s "Data"
    section names both the timer unit and the doc that carries the restore procedure
    (`database.md`); and the phrase "cron container" is gone from both files (BASE: PRD.md:333
    and, historically, docs/deployment.md — task-03 already reworded the latter's "On the box"
    line, but the Data section itself still needs the timer unit name and PRD.md still says "cron
    container").
    """
    prd_text = _require(_PRD)
    deployment_text = _require(_DEPLOYMENT_DOC)

    prd_section_11 = _section(prd_text, "## 11. Deployment", "## 12. Milestones")
    assert "systemd timer" in prd_section_11, (
        "PRD.md §11 does not say 'systemd timer':\n" + prd_section_11
    )

    deployment_data_section = _section(deployment_text, "## Data: backups and retention", "## Logs")
    assert "sentinelbrief-backup.timer" in deployment_data_section, (
        "docs/deployment.md 'Data' section does not name sentinelbrief-backup.timer:\n"
        + deployment_data_section
    )
    assert "database.md" in deployment_data_section, (
        "docs/deployment.md 'Data' section does not mention database.md:\n"
        + deployment_data_section
    )

    assert "cron container" not in prd_text, "PRD.md still says 'cron container'"
    assert "cron container" not in deployment_text, "docs/deployment.md still says 'cron container'"
