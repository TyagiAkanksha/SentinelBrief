"""HMAC-SHA256 request signing (PRD §6.1) — stdlib only, shared by the API, the shipper, and
`scripts/`. `verify_signature` is fail-closed: it never raises, and it never treats an empty
secret, a missing header, or a malformed header as "signing disabled".
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Signature"
_PREFIX = "sha256="


def sign_body(secret: str, body: bytes) -> str:
    """Sign `body` with `secret`, in the `X-Signature` header's own wire format.

    Args:
        secret: The shared HMAC secret.
        body: The exact raw request body bytes to sign.

    Returns:
        `"sha256="` followed by the hex-encoded HMAC-SHA256 digest of `body` under `secret`.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Verify `header` is a valid HMAC-SHA256 signature of `body` under `secret`.

    Fails closed — returns `False`, never raises — for an empty secret, a missing header, a
    malformed header (no `"sha256="` prefix, non-hex digest, wrong length), or a mismatched
    digest.

    Args:
        secret: The shared HMAC secret.
        body: The exact raw request body bytes that were purportedly signed.
        header: The `X-Signature` header value, or `None` if absent.

    Returns:
        `True` only when `secret` is non-empty and `header` is a well-formed signature that
        matches `body` under `secret`.
    """
    if not secret or header is None or not header.startswith(_PREFIX):
        return False
    candidate = header[len(_PREFIX) :]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(candidate, expected)
