"""Pins `scripts/check_real_sessions.py`'s "Suggested follow-ups" section content (m6 task-06
fix-1 I4) — previously the only assertion touching this section was the pinned file's
`assert "Suggested follow-ups" in captured.out`, so all four mandated bullet kinds (Interfaces,
brief lines 86-91) and the whole section body were unpinned; two mutations survived 9/9 tests
(deleting the "extra field on a SUMMARIZED eventid" filter, and replacing the entire section with
a hard-coded `- None.`, which silences the "NEVER edit an existing v1 fixture" clause — the only
place in the code carrying the milestone's Global Constraint to the owner).

Builds `SessionReport` by hand (never through `collect()`) — adding a test this way touches no
pin. `scripts/` has no `__init__.py`, so the module is loaded the same way every other test file
in this task loads it (test files never import from each other).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_real_sessions.py"


def _load_check_real_sessions() -> ModuleType:
    """Load `scripts/check_real_sessions.py` as a standalone module — mirrors the pinned file's
    own `_load_check_real_sessions`."""
    spec = importlib.util.spec_from_file_location("check_real_sessions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_kwargs() -> dict[str, Any]:
    """Every `SessionReport` field at a "clean" (nothing to flag) default."""
    return {
        "n_alerts": 10,
        "n_invalid": 0,
        "invalid_alert_ids": [],
        "invalid_locs": {},
        "eventid_counts": {},
        "unknown_eventids": {},
        "extra_fields_by_eventid": {},
        "extra_envelope_fields": [],
        "n_truncated": 0,
        "truncated_events_total": 0,
        "n_unclosed": 0,
        "events_per_session_p50": 0.0,
        "events_per_session_p95": 0.0,
        "duration_ms_p50": None,
        "duration_ms_p95": None,
        "raw_bytes_mean": 0.0,
        "raw_bytes_total": 0,
        "by_status": {},
    }


def test_followups_include_all_four_bullet_kinds() -> None:
    """A "dirty" report (one of each: unknown eventid, extra field on a summarized eventid, an
    invalid row, a truncated session) renders all four mandated bullets — including the
    milestone's Global Constraint sentence — and the extra-field bullet is correctly scoped to
    ONLY the summarized eventid (mutation (i)'s target: deleting that filter would also emit a
    bullet for `cowrie.client.size` below, which is an "other", not a summarized, eventid).
    """
    module = _load_check_real_sessions()
    report = module.SessionReport(
        **{
            **_base_kwargs(),
            "n_invalid": 1,
            "invalid_alert_ids": ["11111111-1111-1111-1111-111111111111"],
            "invalid_locs": {"src_ip": 1},
            "unknown_eventids": {"cowrie.made.up": 1},
            "extra_fields_by_eventid": {
                "cowrie.session.connect": ["extra1"],  # SUMMARIZED -> bullet expected
                "cowrie.client.size": ["other1"],  # "other" -> no bullet expected
            },
            "n_truncated": 1,
            "truncated_events_total": 5,
        }
    )

    rendered = module.render(report)

    # unknown-eventid bullet (M7: singular "time" for count == 1)
    assert (
        "`cowrie.made.up` seen 1 time — add to cowrie-events.md's 'other events' list" in rendered
    )

    # extra-field-on-a-summarized-eventid bullet, including the Global Constraint sentence
    assert "`cowrie.session.connect.extra1`" in rendered
    assert "NEVER edit an existing v1 fixture" in rendered
    # scoped to summarized eventids only — mutation (i)'s target
    assert "`cowrie.client.size.other1`" not in rendered

    # invalid-row bullet: actionable (ids + first locs), never `raw` (I1)
    assert "1 payload(s) failed `SessionAlert`" in rendered
    assert "11111111-1111-1111-1111-111111111111" in rendered
    assert "src_ip×1" in rendered
    assert "never prints `raw`" in rendered

    # truncation bullet
    assert "1 session(s) were truncated by the shipper" in rendered
    assert "5 events dropped in total" in rendered


def test_followups_section_says_none_when_clean() -> None:
    """A "clean" report (nothing to flag) renders the exact, pinned `- None.` body — mutation
    (j)'s target: replacing the whole section with a hard-coded `- None.` regardless of content
    would make this test pass but `test_followups_include_all_four_bullet_kinds` above fail,
    since together they pin both the "something to say" and the "nothing to say" cases.
    """
    module = _load_check_real_sessions()
    report = module.SessionReport(**_base_kwargs())

    rendered = module.render(report)

    assert rendered.endswith("## Suggested follow-ups\n- None.\n")
