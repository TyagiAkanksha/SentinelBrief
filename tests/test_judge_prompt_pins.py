"""Hash-pins the shipped `evals/prompts/judge-v1.md` (m7 task-03).

CONVENTIONS.md §13 / spine Global Constraints: the judge's prompt is versioned and hash-pinned
exactly like a triage prompt (`tests/test_prompt_pins.py`) — a shipped version is immutable; any
wording change ships as `judge-v2.md` plus a `JUDGE_PROMPT_VERSION` bump, never an in-place edit.

The constant below is deliberately the literal placeholder `"<set at GREEN>"`, not a real sha256:
the task-03 brief (Interfaces → test table, "hash pin" row) authors `judge-v1.md`'s exact bytes at
GREEN, so the test-author cannot know its hash in advance. This makes the test fail RED for two
reasons depending on ordering — `evals/prompts/judge-v1.md` does not exist yet
(`FileNotFoundError`), and even once it does, `"<set at GREEN>"` is not a valid sha256 hex digest
and can never equal one. **The task-03 brief pre-approves the implementer's one-line fill of this
one constant** with the real file's sha256 once it ships (recorded in the implementer's own
report) — that fill is the brief's own Step 5, not a pinned-test edit requiring controller
approval, and must not be treated as one. Do not otherwise edit this file's assertion: once
`judge-v1.md` ships, any further byte-for-byte change is caught by this pin exactly like a triage
prompt's — author `judge-v2.md` instead.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_JUDGE_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "evals" / "prompts"
_JUDGE_V1_SHA256 = "<set at GREEN>"


def test_shipped_judge_v1_hash_pinned() -> None:
    text = (_JUDGE_PROMPTS_DIR / "judge-v1.md").read_bytes()

    assert hashlib.sha256(text).hexdigest() == _JUDGE_V1_SHA256
