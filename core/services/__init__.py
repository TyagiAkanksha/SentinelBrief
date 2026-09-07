"""Business logic that touches the ORM (alerts, verdicts, stats) — session-first, `flush()`-only,
never `commit()`/`rollback()` (CONVENTIONS.md §2/§3)."""

from __future__ import annotations
