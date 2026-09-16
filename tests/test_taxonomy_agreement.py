"""Pins the M7 Global Constraint "one category taxonomy": `docs/labeling-guide.md`, the
`/cowrie-fixture` skill's category paragraph, and `worker/prompts/triage-v4.md`'s category
definitions must share the same seven `VerdictCategory` names AND the same `brute_force`-vs-
`reconnaissance` tie-break sentence, before the author labels a single v2 row (m7 task-01,
`docs/plans/m7-eval-hardening.md` Global Constraints).

`docs/labeling-guide.md` does not exist yet, so this test is RED on `FileNotFoundError` reading
it — the m7 task-01 Expected RED signal — not a collection error (it imports only `core.schemas
.verdict`, already shipped).

The tie-break pin greps the key phrase "derived from this host" (from PRD §7.1/§6.6's rubric
wording: "credential attempts using usernames derived from THIS host ... => reconnaissance") in
the guide and the skill only — `triage-v4.md`'s `reconnaissance` line already says "host-derived
usernames", a different (already-shipped, immutable) wording the Global Constraint explicitly
does not require the prompt to change.

**m7 task-01 fix-1 (review M7, APPROVED edit A(b)):** the original test compared category NAMES
only, so an edit that kept every name but silently changed the guide's `reconnaissance` sentence
away from the prompt's would still pass — exactly the M1 taxonomy drift the Global Constraint
exists to prevent. `test_guide_and_prompt_share_verbatim_category_definitions` below additionally
extracts the seven "- `name` — definition." bullet lines from the guide and from `triage-v4
.md` and asserts they are byte-identical, name for name. The module's paths are also resolved from
the repo root (`Path(__file__).resolve().parent.parent`, matching `tests/test_check_real_sessions
.py`'s own `REPO_ROOT` convention) rather than the relative `Path("docs/…")` used before, which
depended on pytest always being invoked from the repo root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from core.schemas.verdict import VerdictCategory

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDE_PATH = REPO_ROOT / "docs" / "labeling-guide.md"
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "cowrie-fixture" / "SKILL.md"
PROMPT_PATH = REPO_ROOT / "worker" / "prompts" / "triage-v4.md"

CANONICAL_CATEGORIES: frozenset[str] = frozenset(get_args(VerdictCategory))

_DEFINITION_LINE = re.compile(r"^- `(\w+)` — (.+)$", re.MULTILINE)


def _mentioned_categories(text: str) -> frozenset[str]:
    """Every canonical `VerdictCategory` name that appears backtick-quoted in `text`."""
    return frozenset(name for name in CANONICAL_CATEGORIES if f"`{name}`" in text)


def _category_definitions(text: str) -> dict[str, str]:
    """Every "- `name` — definition." bullet line in `text`, keyed by category name."""
    return dict(_DEFINITION_LINE.findall(text))


def test_guide_skill_and_prompt_share_one_taxonomy() -> None:
    guide_text = GUIDE_PATH.read_text()
    skill_text = SKILL_PATH.read_text()
    prompt_text = PROMPT_PATH.read_text()

    guide_categories = _mentioned_categories(guide_text)
    skill_categories = _mentioned_categories(skill_text)
    prompt_categories = _mentioned_categories(prompt_text)

    assert guide_categories == CANONICAL_CATEGORIES, (
        f"docs/labeling-guide.md is missing categories: {CANONICAL_CATEGORIES - guide_categories}"
    )
    assert skill_categories == CANONICAL_CATEGORIES, (
        ".claude/skills/cowrie-fixture/SKILL.md is missing categories: "
        f"{CANONICAL_CATEGORIES - skill_categories}"
    )
    assert prompt_categories == CANONICAL_CATEGORIES, (
        "worker/prompts/triage-v4.md is missing categories: "
        f"{CANONICAL_CATEGORIES - prompt_categories}"
    )
    assert guide_categories == skill_categories == prompt_categories

    assert "derived from this host" in guide_text, (
        "docs/labeling-guide.md must state the brute_force-vs-reconnaissance tie-break "
        "('credential attempts using usernames derived from THIS host ... no success => "
        "reconnaissance')"
    )
    assert "derived from this host" in skill_text, (
        ".claude/skills/cowrie-fixture/SKILL.md's category paragraph must carry the same "
        "tie-break sentence as the labeling guide"
    )


def test_guide_and_prompt_share_verbatim_category_definitions() -> None:
    guide_defs = _category_definitions(GUIDE_PATH.read_text())
    prompt_defs = _category_definitions(PROMPT_PATH.read_text())

    assert set(guide_defs) == CANONICAL_CATEGORIES, (
        f"docs/labeling-guide.md's definition bullets don't cover all 7 categories: "
        f"{CANONICAL_CATEGORIES - set(guide_defs)}"
    )
    assert set(prompt_defs) == CANONICAL_CATEGORIES, (
        f"worker/prompts/triage-v4.md's definition bullets don't cover all 7 categories: "
        f"{CANONICAL_CATEGORIES - set(prompt_defs)}"
    )
    mismatched = {
        name: (guide_defs[name], prompt_defs[name])
        for name in guide_defs
        if guide_defs[name] != prompt_defs.get(name)
    }
    assert not mismatched, (
        "docs/labeling-guide.md's category definitions must be verbatim-identical to "
        f"worker/prompts/triage-v4.md's (the Global Constraint's ONE taxonomy); mismatches: "
        f"{mismatched}"
    )
