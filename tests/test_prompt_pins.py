"""Hash-pins the shipped `triage-v1.md` prompt file (m0 task-04, implementer addition).

CONVENTIONS.md §13: a shipped prompt version is immutable — any wording change must ship as
`triage-v2.md` (plus a config-default bump), never an in-place edit. This test is deliberately a
tripwire: any byte-for-byte change to `worker/prompts/triage-v1.md` (including whitespace) fails
this pin *by design*. If it fails, do not edit this hash to make it pass — author `triage-v2.md`
instead and repoint `TRIAGE_PROMPT_VERSION`.
"""

from __future__ import annotations

import hashlib

from worker.prompts import PROMPTS_DIR

_TRIAGE_V1_SHA256 = "fdc5139eaca90322d7d745d35b2103ef610c1920cc170b888f2754c9063ac34b"
_TRIAGE_V2_SHA256 = "11865e6e5bfc4b7654217bc9f75e67ab62ce31f1f55f7f47e22180f173c134f1"


def test_shipped_v1_hash_pinned() -> None:
    text = (PROMPTS_DIR / "triage-v1.md").read_bytes()

    assert hashlib.sha256(text).hexdigest() == _TRIAGE_V1_SHA256


def test_shipped_v2_hash_pinned() -> None:
    """m1 task-04: `triage-v2.md` is now shipped and immutable — pin its hash the same way v1's
    is pinned above. If this fails, do not edit this hash; author `triage-v3.md` instead."""
    text = (PROMPTS_DIR / "triage-v2.md").read_bytes()

    assert hashlib.sha256(text).hexdigest() == _TRIAGE_V2_SHA256
