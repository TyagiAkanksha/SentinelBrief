"""Pins `core/signing.py`: stdlib HMAC-SHA256 sign/verify, fail-closed (PRD §6.1) — m2 task-03.

`sign_body` is a pure formatting function; `verify_signature` is the fail-closed half — it must
return `False` (never raise, never "fail open") for a missing header, a malformed header, a
tampered body, or an empty secret. The empty-secret case is the one an implementer could get
backwards by accident: an empty `INGEST_HMAC_SECRET` must never accept every request as if
signing were disabled.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from core.signing import sign_body, verify_signature


def test_sign_body_is_sha256_prefixed_hex() -> None:
    body = b'{"hello": "world"}'

    signature = sign_body("k", body)

    expected = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert signature == expected


def test_verify_accepts_valid() -> None:
    body = b'{"hello": "world"}'
    header = sign_body("secret", body)

    assert verify_signature("secret", body, header) is True


def test_verify_rejects_tampered_body() -> None:
    header = sign_body("secret", b'{"a": 1}')

    assert verify_signature("secret", b'{"a": 2}', header) is False


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "md5=abcd",
        "sha256=zz",
        "sha256=" + "0" * 63,
        "sha256=" + "\xe9" * 64,
    ],
    ids=[
        "none",
        "empty",
        "empty-hex",
        "wrong-algo",
        "non-hex",
        "wrong-length",
        "non-ascii",
    ],
)
def test_verify_rejects_missing_or_malformed_header(header: str | None) -> None:
    assert verify_signature("secret", b"body", header) is False


def test_verify_rejects_empty_secret() -> None:
    body = b"body"
    # A header that *would* verify against the empty secret if verification fail-opened on it —
    # pinning that an empty secret is never treated as "signing disabled".
    header = sign_body("", body)

    assert verify_signature("", body, header) is False
