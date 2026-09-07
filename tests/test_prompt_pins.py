"""Hash-pins every shipped `triage-vN.md` prompt file (m0 task-04, implementer addition).

CONVENTIONS.md §13: a shipped prompt version is immutable — any wording change must ship as the
next `triage-v(N+1).md` (plus a config-default bump), never an in-place edit. These tests are
deliberately tripwires: any byte-for-byte change to a shipped `worker/prompts/triage-vN.md`
(including whitespace) fails its pin *by design*. If one fails, do not edit its hash to make it
pass — author the next version instead and repoint `TRIAGE_PROMPT_VERSION`.
"""

from __future__ import annotations

import hashlib

from worker.prompts import PROMPTS_DIR

_TRIAGE_V1_SHA256 = "fdc5139eaca90322d7d745d35b2103ef610c1920cc170b888f2754c9063ac34b"
_TRIAGE_V2_SHA256 = "11865e6e5bfc4b7654217bc9f75e67ab62ce31f1f55f7f47e22180f173c134f1"
_TRIAGE_V3_SHA256 = "334e47bdeb2d390cb5e3f4ea7628b3373cc95b77b23e62147e63023081dd6e46"


def test_shipped_v1_hash_pinned() -> None:
    text = (PROMPTS_DIR / "triage-v1.md").read_bytes()

    assert hashlib.sha256(text).hexdigest() == _TRIAGE_V1_SHA256


def test_shipped_v2_hash_pinned() -> None:
    """m1 task-04: `triage-v2.md` is now shipped and immutable — pin its hash the same way v1's
    is pinned above. If this fails, do not edit this hash; author `triage-v3.md` instead."""
    text = (PROMPTS_DIR / "triage-v2.md").read_bytes()

    assert hashlib.sha256(text).hexdigest() == _TRIAGE_V2_SHA256


def test_shipped_v3_hash_pinned() -> None:
    """m1 final-review fix wave: `triage-v3.md` is now shipped and immutable — pin its hash the
    same way v1's and v2's are pinned above. It carries v1's severity rubric and output contract
    unchanged; only the category definitions were aligned to the golden-set standard (I3). If
    this fails, do not edit this hash; author `triage-v4.md` instead."""
    text = (PROMPTS_DIR / "triage-v3.md").read_bytes()

    assert hashlib.sha256(text).hexdigest() == _TRIAGE_V3_SHA256
