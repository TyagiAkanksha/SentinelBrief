"""HMAC-SHA256 request signing — vendored copy of `core/signing.py::sign_body` (m6 task-02).

The shipper never imports the parent SentinelBrief repo (`tests/test_shipper_isolation.py` pins
this with an AST walk), so this is a byte-for-byte duplicate of the one algorithm it needs:
signing outgoing payloads. It never verifies a signature — the shipper is a client, never a
server — so `verify_signature` is deliberately not vendored here.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Signature"
_PREFIX = "sha256="


def sign_body(secret: str, body: bytes) -> str:
    """Sign `body` with `secret`, in the `X-Signature` header's own wire format.

    Args:
        secret: The shared HMAC secret (`INGEST_HMAC_SECRET`).
        body: The exact raw payload bytes to sign.

    Returns:
        `"sha256="` followed by the hex-encoded HMAC-SHA256 digest of `body` under `secret`.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"
