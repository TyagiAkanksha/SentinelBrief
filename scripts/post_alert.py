"""`python scripts/post_alert.py <fixture.json> [--url http://127.0.0.1:8000]` — sign one alert
fixture and POST it to `POST /api/v1/alerts` (PRD §6.1) — m2 task-03.

Reads `INGEST_HMAC_SECRET` from the environment only (no dotenv dependency — the README shows the
`export` line, no way to silently pick up a stray `.env`). `main()`'s `transport` parameter is the
seam `tests/test_post_alert.py` injects an `httpx.MockTransport` through; the default is the real
network. A missing secret or an unreadable fixture file fails with one clean stderr line and exit
code 1, never a traceback, before any network attempt is made.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from core.signing import SIGNATURE_HEADER, sign_body

_DEFAULT_URL = "http://127.0.0.1:8000"


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Sign and POST one alert fixture to `POST /api/v1/alerts`; return the process exit code.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        transport: An `httpx.BaseTransport` to route the request through instead of the real
            network — the seam tests inject `httpx.MockTransport` through.

    Returns:
        `0` on a `2xx` response; `1` when `INGEST_HMAC_SECRET` is unset, the fixture file is
        unreadable, or the response status is not `2xx`.
    """
    parser = argparse.ArgumentParser(prog="post_alert.py")
    parser.add_argument("fixture_path")
    parser.add_argument("--url", default=_DEFAULT_URL)
    args = parser.parse_args(argv)

    secret = os.environ.get("INGEST_HMAC_SECRET")
    if not secret:
        print("error: INGEST_HMAC_SECRET is not set", file=sys.stderr)
        return 1

    try:
        body = Path(args.fixture_path).read_bytes()
    except OSError as e:
        print(f"error: could not read {args.fixture_path}: {e}", file=sys.stderr)
        return 1

    headers = {SIGNATURE_HEADER: sign_body(secret, body), "content-type": "application/json"}
    with httpx.Client(transport=transport) as client:
        response = client.post(f"{args.url}/api/v1/alerts", content=body, headers=headers)

    print(f"{response.status_code} {response.text}")
    return 0 if 200 <= response.status_code < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
