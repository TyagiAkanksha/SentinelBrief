"""Pins `infra/deploy/env-checklist.md` against the m6 task-03 brief's Interfaces block: every
`core.config.Settings` field and a fixed set of non-Settings infra variables are named (in
backticks) somewhere in the checklist, `TEST_DATABASE_URL`/`TEST_REDIS_URL` are flagged "never set
on a deployed service" on the line that names them, and the file never carries a value-shaped
secret (`.claude/rules/infra.md`: "No secret values anywhere in the repo").
"""

from __future__ import annotations

import re
from pathlib import Path

from core.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CHECKLIST = _REPO_ROOT / "infra" / "deploy" / "env-checklist.md"

_INFRA_VARS = (
    "FORWARDED_ALLOW_IPS",
    "POSTGRES_PASSWORD",
    "NEXT_PUBLIC_API_URL",
    "API_URL",
    "SHIPPER_INGEST_URL",
    "ADMIN_TOKEN",
)

_TEST_ONLY_VARS = ("TEST_DATABASE_URL", "TEST_REDIS_URL")

# `sk-...` API keys and `postgresql://user:pass@...` DSNs are the two value shapes CONVENTIONS.md
# §7 names as SecretStr fields whose real value must never be committed; a bare 32+-char
# hex/base64-looking run outside a `<...>` placeholder would similarly read as a leaked token or
# password even if it happens to be synthetic.
_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9]{20,}")
_DSN_WITH_PASSWORD_RE = re.compile(r"postgresql://[^<\s]+:[^<\s]+@")
_HEX_OR_BASE64_RUN_RE = re.compile(r"\b[A-Za-z0-9+/]{32,}\b")


def _read_checklist() -> str:
    assert _CHECKLIST.exists(), (
        f"{_CHECKLIST} does not exist yet — task-03's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    return _CHECKLIST.read_text()


def test_every_settings_field_and_infra_var_listed_once() -> None:
    """Brief Interfaces: every `core.config.Settings` field (uppercased, backticked) appears in
    the checklist, so a shipped setting can never go undocumented for the person running
    `fetch-secrets.sh`/wiring SSM; the fixed infra-only variable names and both `TEST_*` DB/Redis
    vars (each flagged, on its own line, as never belonging on a deployed service — the dedicated
    throwaway test database/Redis must never be mistaken for a production wiring target) are
    present too.
    """
    text = _read_checklist()

    missing = [name.upper() for name in Settings.model_fields if f"`{name.upper()}`" not in text]
    assert not missing, f"Settings field(s) not listed in env-checklist.md: {sorted(missing)}"

    for var in _INFRA_VARS:
        assert f"`{var}`" in text, f"{var} not listed in env-checklist.md"

    for var in _TEST_ONLY_VARS:
        matching_lines = [line for line in text.splitlines() if f"`{var}`" in line]
        assert matching_lines, f"{var} not listed in env-checklist.md"
        assert any("never set on a deployed service" in line for line in matching_lines), (
            f"{var}'s line does not say 'never set on a deployed service': {matching_lines}"
        )


def test_checklist_carries_no_value_shaped_secret() -> None:
    """Brief Interfaces: no `sk-...`-shaped API key, no `postgresql://user:pass@...` DSN with a
    password, and no bare 32+-char hex/base64-looking run outside a `<...>` placeholder — the
    checklist names every variable's source, never its value.
    """
    text = _read_checklist()

    assert not _SK_KEY_RE.search(text), "checklist contains an sk-...-shaped API key"
    assert not _DSN_WITH_PASSWORD_RE.search(text), (
        "checklist contains a postgresql://user:pass@... DSN with a password"
    )

    placeholder_spans = [match.span() for match in re.finditer(r"<[^<>]+>", text)]

    def _inside_a_placeholder(span: tuple[int, int]) -> bool:
        return any(
            placeholder_start <= span[0] and span[1] <= placeholder_end
            for placeholder_start, placeholder_end in placeholder_spans
        )

    for match in _HEX_OR_BASE64_RUN_RE.finditer(text):
        assert _inside_a_placeholder(match.span()), (
            f"32+-char hex/base64-looking run outside a <placeholder>: {match.group()!r}"
        )


def test_backup_bucket_listed() -> None:
    """m6 task-04 brief, Interfaces -> test table, `checklist` row: a `BACKUP_S3_BUCKET` row
    exists (added under a "Host (root) — backup timer" table per the task-04 Files list) and its
    Secret? cell is marked non-secret — matching this table's own shape, where every non-secret
    cell starts with `N` and every secret cell starts with `**Y**` (see `POSTGRES_USER`/
    `POSTGRES_PASSWORD` above) — because a bucket name is instance-specific but not a credential.
    """
    text = _read_checklist()

    row_lines = [
        line
        for line in text.splitlines()
        if line.strip().startswith("|") and "`BACKUP_S3_BUCKET`" in line
    ]
    assert row_lines, "BACKUP_S3_BUCKET row not present in env-checklist.md"

    cells = [cell.strip() for cell in row_lines[0].split("|")]
    # A `| Variable | Secret? | Where | Value |` row splits (on "|") into
    # ["", "`VAR`", "Secret?", "Where", "Value", ""] — index 2 is the Secret? cell.
    secret_cell = cells[2]
    assert secret_cell.startswith("N"), (
        f"BACKUP_S3_BUCKET's Secret? cell is not marked non-secret: {secret_cell!r}"
    )
    assert not secret_cell.startswith("**Y**"), (
        f"BACKUP_S3_BUCKET's Secret? cell reads as secret: {secret_cell!r}"
    )
