"""Env-roster test cited by CONVENTIONS.md §7 and `.claude/rules/core.md` (m1 task-00; M0
final-review plan defect 5): `.env.example` must document every `core.config.Settings` field, and
must never carry an uncommented variable that is neither a `Settings` field nor a name explicitly
scheduled for a later milestone — so a typo'd env var name cannot hide silently in either
direction.

Both tests read the real, tracked `.env.example` (no fixture, no mock: this is the file
`git diff` sees) and are expected **green on arrival** — M1 adds no `Settings` field.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.config import Settings

_ENV_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / ".env.example"

# Uncommented variables in .env.example that are NOT (yet) `Settings` fields: read by milestones
# after M1, or by non-Settings consumers (compose, the web container). Verified against the
# tracked file's current uncommented `NAME=` lines as of m1 task-00. If a name here stops
# appearing in .env.example, or a new uncommented name appears that isn't listed here, that is a
# real drift the second test below must catch. A name graduates OUT of this set in the same
# commit its field lands on `Settings` (m2 task-01 fix r1: `DATABASE_URL` graduated when
# `core.config.Settings.database_url` shipped; m5 task-01: `REDIS_URL` graduates the same way) —
# a stale entry here would silently stop guarding against a typo in that name.
_SCHEDULED = {
    "ESCALATE_SEVERITY_GTE",
    "ESCALATE_CONFIDENCE_LT",
    "DAILY_TOKEN_BUDGET",
    "ADMIN_TOKEN",
    "RETRIAGE_PER_DAY",
    "PUBLIC_RATE_LIMIT_PER_MIN",
    "NEXT_PUBLIC_API_URL",
    "API_URL",
}

# An uncommented assignment line: "NAME=...". Indented example lines inside comments
# (e.g. "#   LLM_BASE_URL=...") do not match — they start with "#", not a bare NAME.
_UNCOMMENTED_ASSIGNMENT = re.compile(r"^([A-Z_][A-Z0-9_]*)=")


def _env_example_lines() -> list[str]:
    return _ENV_EXAMPLE_PATH.read_text().splitlines()


def _documented_line_pattern(env_name: str) -> re.Pattern[str]:
    """Matches a line documenting `env_name`, commented or not: "NAME=", "#NAME=", "# NAME="."""
    return re.compile(rf"^#? ?{re.escape(env_name)}=")


def test_every_settings_field_documented_in_env_example() -> None:
    lines = _env_example_lines()

    missing = []
    for name in Settings.model_fields:
        env_name = name.upper()
        pattern = _documented_line_pattern(env_name)
        if not any(pattern.match(line) for line in lines):
            missing.append(env_name)

    assert not missing, (
        f"Settings field(s) not documented in .env.example: {sorted(missing)} "
        "— add a line for each (CONVENTIONS.md §7)."
    )


def test_env_example_has_no_unknown_settings_lines() -> None:
    lines = _env_example_lines()
    settings_names = {name.upper() for name in Settings.model_fields}

    found: set[str] = set()
    for line in lines:
        match = _UNCOMMENTED_ASSIGNMENT.match(line)
        if match:
            found.add(match.group(1))

    unknown = found - settings_names - _SCHEDULED

    assert not unknown, (
        f"Unrecognized uncommented variable(s) in .env.example: {sorted(unknown)} — not a "
        "Settings field and not in the _SCHEDULED allowlist. Fix the typo, or add the name to "
        "_SCHEDULED if it is a deliberate non-Settings variable."
    )
