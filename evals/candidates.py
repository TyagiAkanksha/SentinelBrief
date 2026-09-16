"""`evals.candidates`: the DB-free `Candidate` shape shared by the sampler and the label tool
(PRD §7.1, §13; m7 task-01 fix-1 ruling R14).

The review's M9 finding: `evals/label_tool.py` imported `Candidate`/`STRATA_CATEGORIES` from
`evals.sample`, which imports `sqlalchemy`/`core.db`/`core.models` — so the author's
workstation-side labeling CLI could not even be imported without the DB stack installed. This
module holds `Candidate`, `STRATA_CATEGORIES`, `stratum_id`, `INJECTION_HINT` and
`matches_injection_hint` — everything about a sampled candidate's *identity, shape and
injection-hint detection* that has nothing to do with reading the database — so `evals/sample.py`
(the DB-reading half) and `evals/label_tool.py`/`evals/label_render.py` (the file-reading, DB-free
half) both import from here instead of one importing the other. Ruling N2 (fix-3 re-review): the
label tool re-runs `matches_injection_hint` against a candidate's OWN alert to decide whether to
offer the `injection` tag, in both `label` and `rereview` — never from a stored stratum or a
first-pass label, so a re-reviewed case's tag offer can never depend on what the author typed last
time.

`stratum_id` is ruling R10's opaque token: the plain stratum name (a category from
`STRATA_CATEGORIES`, `"injection-candidate"` or `"unverdicted"`) IS the cheap model's category by
value, so neither the candidate file nor `render_case` may ever carry or print it verbatim — only
this short, seed-salted hash may reach disk or the labeler's screen.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from core.schemas.alert import SessionAlert
from core.schemas.verdict import VerdictCategory

STRATA_CATEGORIES: tuple[VerdictCategory, ...] = (
    "scanning",
    "brute_force",
    "reconnaissance",
    "successful_intrusion",
    "malware_delivery",
    "persistence_attempt",
    "other",
)

INJECTION_HINT = re.compile(
    r"ignore (all |previous |prior )?instructions|system prompt|as an ai"
    r"|severity ?[:=] ?[1-5]|rate (this|it) (as )?(low|benign|1)",
    re.I,
)
"""Ruling R11: no bare `assistant` (matched too much innocuous text, e.g. `assistant_manager`);
`severity` now requires a literal `:` or `=` before the digit (`severity 3` alone no longer
matches) — a hint for the sampling stratum (and, per ruling N2, the label tool's tag-offer prompt)
only, never a label; the human decides the `injection` tag (`docs/labeling-guide.md`)."""


def matches_injection_hint(alert: SessionAlert) -> bool:
    """Whether any event's `username`/`input` carries instruction-like text (PRD §10.6)."""
    for event in alert.events:
        if event.username is not None and INJECTION_HINT.search(event.username):
            return True
        if event.input is not None and INJECTION_HINT.search(event.input):
            return True
    return False


@dataclass(frozen=True)
class Candidate:
    """One session sampled for v2 labeling: identity, provenance, and the raw alert only.

    `stratum` is `"<cheap category>"` (one of `STRATA_CATEGORIES`), `"injection-candidate"`, or
    `"unverdicted"` — never a verdict field itself, and never written to disk or rendered by name
    (ruling R10): only `stratum_id(stratum, seed)` ever leaves this process's memory.
    """

    case_id: str
    alert_id: str
    received_at: datetime
    stratum: str
    alert: SessionAlert


def stratum_id(stratum: str, seed: int) -> str:
    """The obfuscated, per-`(stratum, seed)` token a candidate file may carry (ruling R10).

    `sha256(f"{seed}:{stratum}")`'s first 8 hex characters — short, stable for a given
    `(stratum, seed)` pair. This is obfuscation, not secrecy (review N5): the seed travels in
    every row alongside the token, so the mapping IS recoverable by design — by anyone holding the
    file, over the finite set of known stratum names (`STRATA_CATEGORIES` plus
    `"injection-candidate"`/`"unverdicted"`), no secret required (`evals/label_tool.py`'s
    `_load_candidates` recovers it this way, for provenance). The property this defends is that no
    category name is ever readable on screen, or in the raw file bytes, while labeling — it
    defends a cooperating labeler against anchoring, not an adversary against a determined decode.

    Args:
        stratum: The stratum name to encode.
        seed: The sampling run's seed.

    Returns:
        An 8-hex-character opaque token.
    """
    digest = hashlib.sha256(f"{seed}:{stratum}".encode()).hexdigest()
    return digest[:8]
