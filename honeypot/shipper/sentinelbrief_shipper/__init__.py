"""sentinelbrief_shipper: the honeypot log shipper (m6 task-02).

Tails Cowrie's JSON log, groups events by session, and POSTs one HMAC-signed `SessionAlert` per
closed session to SentinelBrief's ingest URL. Imports stdlib and `httpx` only — never anything
from the parent SentinelBrief repo (`tests/test_shipper_isolation.py` pins this with an AST walk).
"""

from __future__ import annotations

__version__ = "0.1.0"
