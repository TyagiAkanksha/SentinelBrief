"""`evals.candidates`: the DB-free `Candidate` shape shared by the sampler and the label tool
(PRD §7.1, §13; m7 task-01 fix-1 ruling R14).

The review's M9 finding: `evals/label_tool.py` imported `Candidate`/`STRATA_CATEGORIES` from
`evals.sample`, which imports `sqlalchemy`/`core.db`/`core.models` — so the author's
workstation-side labeling CLI could not even be imported without the DB stack installed. This
module holds `Candidate`, `STRATA_CATEGORIES` and `stratum_id` — everything about a sampled
candidate's *identity and shape* that has nothing to do with reading the database — so
`evals/sample.py` (the DB-reading half) and `evals/label_tool.py`/`evals/label_render.py` (the
file-reading, DB-free half) both import from here instead of one importing the other.

`stratum_id` is ruling R10's opaque token: the plain stratum name (a category from
`STRATA_CATEGORIES`, `"injection-candidate"` or `"unverdicted"`) IS the cheap model's category by
value, so neither the candidate file nor `render_case` may ever carry or print it verbatim — only
this short, seed-salted hash may reach disk or the labeler's screen.
"""

from __future__ import annotations

import hashlib
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
    """The opaque, per-`(stratum, seed)` token a candidate file may carry (ruling R10).

    `sha256(f"{seed}:{stratum}")`'s first 8 hex characters — short, stable for a given
    `(stratum, seed)` pair, and recoverable only by code that already knows the seed and the
    finite set of stratum names (`STRATA_CATEGORIES` plus `"injection-candidate"`/
    `"unverdicted"`); the raw candidate file never names a stratum by value.

    Args:
        stratum: The stratum name to encode.
        seed: The sampling run's seed.

    Returns:
        An 8-hex-character opaque token.
    """
    digest = hashlib.sha256(f"{seed}:{stratum}".encode()).hexdigest()
    return digest[:8]
