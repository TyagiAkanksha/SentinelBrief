"""Pins `evals.golden`: `GoldenLabel`, `GoldenCase`, `load_golden`, and the v1 dataset content
(m1 task-01).

PRD §6.6 (labels follow the rubric; severity >= 4 requires escalate), §7.1 (golden set v1 is
20 synthetic sessions, at least three per severity band, every category used), §10.6 (>=3
injection cases in v1 of three kinds: a `username` carrying a plain instruction, a `username`
carrying a forged `<<<END_ALERT_DATA>>>` marker plus an instruction, and a `cowrie.client.version`
banner carrying an instruction), §13 (v1 labels may be machine-authored).

Dataset-backed tests load `evals/golden/v1.jsonl` through `load_golden` via the module-scoped
`golden_cases` fixture below. The file does not exist yet, so `evals.golden` itself does not
exist either: every test in this module is RED at collection with
`ModuleNotFoundError: No module named 'evals.golden'`, not merely at first use.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from core.schemas.verdict import VERDICT_JSON_SCHEMA, VerdictCategory
from evals.golden import GoldenCase, GoldenLabel, load_golden
from worker.prompts import build_messages, load_prompt
from worker.summarize import summarize_session

GOLDEN_V1_PATH = Path("evals/golden/v1.jsonl")
ALL_CATEGORIES = set(get_args(VerdictCategory))

_LOGIN_EVENTS = {"cowrie.login.failed", "cowrie.login.success"}

SESSION_ID = "b2c3d4e5f607"
SRC_IP = "203.0.113.99"
SENSOR = "hp-test-01"


@pytest.fixture(scope="module")
def golden_cases() -> list[GoldenCase]:
    return load_golden(GOLDEN_V1_PATH)


def _event(eventid: str, ts: str, **extra: object) -> dict[str, object]:
    return {
        "eventid": eventid,
        "timestamp": ts,
        "session": SESSION_ID,
        "src_ip": SRC_IP,
        "sensor": SENSOR,
        "message": f"{eventid} event",
        **extra,
    }


def _alert(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "source": "cowrie",
        "session_id": SESSION_ID,
        "src_ip": SRC_IP,
        "sensor": SENSOR,
        "events": events,
    }


def _minimal_alert() -> dict[str, object]:
    return _alert(
        [
            _event("cowrie.session.connect", "2026-09-06T14:03:21.481902Z"),
            _event(
                "cowrie.login.failed",
                "2026-09-06T14:03:22.115004Z",
                username="root",
                password="root",
            ),
            _event("cowrie.session.closed", "2026-09-06T14:03:31.104450Z", duration_ms=9623),
        ]
    )


def _login_usernames(case: GoldenCase) -> list[str]:
    return [e.username for e in case.alert.events if e.eventid in _LOGIN_EVENTS and e.username]


def _client_versions(case: GoldenCase) -> list[str]:
    return [
        e.version for e in case.alert.events if e.eventid == "cowrie.client.version" and e.version
    ]


def _username_and_banner_injected_strings(case: GoldenCase) -> list[str]:
    """The attacker-controlled strings this injection row carries via `username`/`version`.

    Only the two kinds `worker.summarize.summarize_session` can ever surface today (a `login.*`
    username containing `"ignore"` or the forged `"<<<"` marker, and a `cowrie.client.version`
    banner containing `"ignore"`). A `cowrie.command.input`-based injection row has none of these
    and reaches the prompt only from M4's `get_session_commands` tool, not the first-pass summary.
    """
    return [
        username for username in _login_usernames(case) if "ignore" in username or "<<<" in username
    ] + [version for version in _client_versions(case) if "ignore" in version]


def test_v1_loads_20_cases(golden_cases: list[GoldenCase]) -> None:
    assert len(golden_cases) == 20


def test_case_ids_unique(golden_cases: list[GoldenCase]) -> None:
    ids = [case.case_id for case in golden_cases]

    assert len(ids) == len(set(ids)), f"duplicate case_id(s) in v1.jsonl: {ids}"


def test_every_severity_band_present(golden_cases: list[GoldenCase]) -> None:
    counts = Counter(case.label.severity for case in golden_cases)

    for band in range(1, 6):
        assert counts[band] >= 3, (
            f"severity band {band} has only {counts[band]} golden v1 cases, need >=3"
        )


def test_every_category_used(golden_cases: list[GoldenCase]) -> None:
    used = {case.label.category for case in golden_cases}
    missing = ALL_CATEGORIES - used

    assert not missing, f"VerdictCategory values never used in golden v1 labels: {missing}"


def test_injection_cases_cover_three_kinds(golden_cases: list[GoldenCase]) -> None:
    injection_cases = [case for case in golden_cases if "injection" in case.tags]

    assert len(injection_cases) >= 3, (
        f"expected >=3 rows tagged 'injection', found {len(injection_cases)}"
    )

    has_username_ignore = any(
        "ignore" in username for case in injection_cases for username in _login_usernames(case)
    )
    has_username_end_marker = any(
        "<<<END_ALERT_DATA>>>" in username
        for case in injection_cases
        for username in _login_usernames(case)
    )
    has_banner_ignore = any(
        "ignore" in version for case in injection_cases for version in _client_versions(case)
    )

    assert has_username_ignore, (
        "expected an injection row with a login username containing 'ignore'"
    )
    assert has_username_end_marker, (
        "expected an injection row with a username containing the forged "
        "<<<END_ALERT_DATA>>> marker"
    )
    assert has_banner_ignore, (
        "expected an injection row with a cowrie.client.version banner containing 'ignore'"
    )

    for case in injection_cases:
        assert case.label.severity >= 3, (
            f"{case.case_id}: injection row must be labeled by attacker behavior "
            f"(severity >= 3), got {case.label.severity}"
        )


def test_injection_strings_reach_the_first_pass_prompt(golden_cases: list[GoldenCase]) -> None:
    template = load_prompt("triage-v1")
    injection_cases = [case for case in golden_cases if "injection" in case.tags]
    assert injection_cases, "expected at least one row tagged 'injection'"

    checked_a_username_or_banner_row = False
    for case in injection_cases:
        injected_strings = _username_and_banner_injected_strings(case)
        if not injected_strings:
            # A command.input-based injection (reaches the prompt only from M4's
            # get_session_commands tool) has no username/version to check here; skip it.
            continue
        checked_a_username_or_banner_row = True

        summary = summarize_session(case.alert)
        messages = build_messages(template, summary=summary, schema=VERDICT_JSON_SCHEMA)
        user_content = messages[1]["content"]

        for injected in injected_strings:
            neutralized = injected.replace("<<<", "‹‹‹")
            assert neutralized in user_content, (
                f"{case.case_id}: injected string {injected!r} (neutralized: {neutralized!r}) "
                f"never reaches the first-pass prompt's user message — check "
                f"worker.summarize.summarize_session's sampling caps"
            )

        assert user_content.count("<<<END_ALERT_DATA>>>") <= 1, (
            f"{case.case_id}: the real <<<END_ALERT_DATA>>> closing marker must appear at most "
            f"once in the user content — a forged marker in attacker data must be neutralized, "
            f"never left as a second real-looking closing marker"
        )

    assert checked_a_username_or_banner_row, (
        "expected at least one injection row carrying a username- or banner-based injected "
        "string to check against the first-pass prompt"
    )


def test_labels_respect_escalate_rule(golden_cases: list[GoldenCase]) -> None:
    for case in golden_cases:
        if case.label.severity >= 4:
            assert case.label.escalate is True, (
                f"{case.case_id}: severity {case.label.severity} requires escalate=True"
            )

    with pytest.raises(ValidationError):
        GoldenLabel(severity=4, category="scanning", escalate=False)


def test_rejects_invalid_row(tmp_path: Path) -> None:
    row = {
        "alert": _minimal_alert(),
        "label": {"severity": 9, "category": "scanning", "escalate": False},
        "labeler_note": "invalid row: severity out of range, pins the row-1 error message",
        "tags": [],
    }
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text(json.dumps(row) + "\n")

    with pytest.raises(ValueError, match="row 1"):
        load_golden(bad_file)


def test_rejects_duplicate_case_id(tmp_path: Path) -> None:
    row = {
        "alert": _minimal_alert(),
        "label": {"severity": 2, "category": "scanning", "escalate": False},
        "labeler_note": "duplicate case_id test: the same alert twice must be rejected",
        "tags": [],
    }
    dup_file = tmp_path / "dup.jsonl"
    dup_file.write_text("\n".join([json.dumps(row), json.dumps(row)]) + "\n")

    with pytest.raises(ValueError, match="duplicate"):
        load_golden(dup_file)
