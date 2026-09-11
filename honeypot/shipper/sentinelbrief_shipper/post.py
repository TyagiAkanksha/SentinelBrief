"""Poster (m6 task-02): POSTs one signed payload to the ingest URL and maps the response to a
retry decision. Never raises — a transport-level failure (connect/read/timeout) is just another
retryable outcome, the same as a `503`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from sentinelbrief_shipper import __version__
from sentinelbrief_shipper.signing import SIGNATURE_HEADER, sign_body

# 408/425/429: transient client-side conditions worth retrying; every 5xx is the api's own.
_RETRYABLE_STATUSES = frozenset({408, 425, 429})


@dataclass(frozen=True)
class PostResult:
    """The outcome of one `Poster.post` call."""

    status: int | None
    retry: bool


class Poster:
    """POSTs one signed payload to the ingest URL over a synchronous `httpx.Client`."""

    def __init__(
        self,
        url: str,
        secret: str,
        *,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build the underlying `httpx.Client`.

        Args:
            url: The ingest URL to POST every payload to.
            secret: The HMAC secret every payload is signed with (`INGEST_HMAC_SECRET`).
            timeout_s: Per-request timeout, in seconds.
            transport: An `httpx.BaseTransport` to route requests through instead of the real
                network — tests inject `httpx.MockTransport` through this.
        """
        self._url = url
        self._secret = secret
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def post(self, payload: bytes) -> PostResult:
        """POST `payload`, signed over its exact bytes, and map the response to a retry decision.

        Args:
            payload: The exact signable bytes to send as the request body.

        Returns:
            `PostResult(status, retry)`: `200`/`202` never retry; `408`/`425`/`429` and every
            5xx retry; any other 4xx (permanently rejected) never retries; a transport-level
            `httpx.HTTPError` (no response at all) maps to `(None, True)`. Never raises.
        """
        headers = {
            SIGNATURE_HEADER: sign_body(self._secret, payload),
            "content-type": "application/json",
            "user-agent": f"sentinelbrief-shipper/{__version__}",
        }
        try:
            response = self._client.post(self._url, content=payload, headers=headers)
        except httpx.HTTPError:
            return PostResult(status=None, retry=True)

        status = response.status_code
        if status in (200, 202):
            return PostResult(status=status, retry=False)
        if status in _RETRYABLE_STATUSES or status >= 500:
            return PostResult(status=status, retry=True)
        return PostResult(status=status, retry=False)

    def close(self) -> None:
        """Close the underlying `httpx.Client`."""
        self._client.close()
