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
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

from core.schemas.verdict import VerdictCategory

GUIDE_PATH = Path("docs/labeling-guide.md")
SKILL_PATH = Path(".claude/skills/cowrie-fixture/SKILL.md")
PROMPT_PATH = Path("worker/prompts/triage-v4.md")

CANONICAL_CATEGORIES: frozenset[str] = frozenset(get_args(VerdictCategory))


def _mentioned_categories(text: str) -> frozenset[str]:
    """Every canonical `VerdictCategory` name that appears backtick-quoted in `text`."""
    return frozenset(name for name in CANONICAL_CATEGORIES if f"`{name}`" in text)


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
