"""`python scripts/fetch_geoip.py [--out-dir DIR] [--edition NAME ...]` — deploy-time helper that
downloads the GeoLite2 Country and ASN `.mmdb` databases from MaxMind (PRD §11, §10.8) into a
directory `infra/docker-compose.yml`'s `api` service bind-mounts read-only. Runs on the host / the
deploy box, never inside the image; the `.mmdb` files it writes are never committed
(`.gitignore`/`.dockerignore` both carry `*.mmdb`) — m4 task-03.

Reads `MAXMIND_LICENSE_KEY` from `Settings()` (environment only) and refuses — before any network
access — when it is unset. The key travels in the download request's query string only; it is
never printed to stdout/stderr and never logged (the `httpx`/`httpcore` loggers, which otherwise
log the full request URL including query params at INFO, are quieted before any request is made).
A transport exception's message can itself embed the key-bearing URL, so only the exception
class's name is ever printed.

`scripts/` has no `__init__.py`; `tests/test_fetch_geoip.py` loads this file via
`importlib.util.spec_from_file_location`, mirroring `scripts/post_alert.py` /
`tests/test_post_alert.py`. `main`'s `transport` parameter is the seam tests inject an
`httpx.MockTransport` through — the real MaxMind endpoint is never touched in tests
(`tests/test_geo_live.py` is the `@pytest.mark.live` exception).
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import tarfile
from collections.abc import Sequence
from pathlib import Path

import httpx

from core.config import Settings

EDITIONS = ("GeoLite2-Country", "GeoLite2-ASN")
DOWNLOAD_URL = "https://download.maxmind.com/app/geoip_download"
DEFAULT_OUT_DIR = Path("infra/geoip")


def _member_matching_edition(names: list[str], edition: str) -> str | None:
    """The archive member name matching `edition` — ends with `f"/{edition}.mmdb"` or equals it —
    or `None` when no member matches.
    """
    suffix = f"/{edition}.mmdb"
    for name in names:
        if name == f"{edition}.mmdb" or name.endswith(suffix):
            return name
    return None


class _ArchiveError(Exception):
    """The one reason `_extract_mmdb` ever fails: no member matches `edition`
    (`"no .mmdb in archive"`), or a matching member is unusable — not a regular file, or the
    archive itself is unreadable (`"bad archive"`). `main` catches this type alone (review
    findings M2/M3): a private exception means an unrelated builtin `LookupError`/`KeyError`
    surfacing from inside `tarfile` can never be silently relabelled as one of these two reasons.
    """


def _extract_mmdb(archive_bytes: bytes, edition: str, out_dir: Path) -> int:
    """Extract the one `.mmdb` member matching `edition` from `archive_bytes` into
    `out_dir / f"{edition}.mmdb"` — always by that fixed, controlled name, never by the member's
    own path (no path traversal is possible regardless of what the archive names its member).

    Returns:
        The number of bytes written.

    Raises:
        _ArchiveError: no member matches `edition`, the matching member is not a regular file
            (e.g. a directory or a symlink — `tar.extractfile()` returns `None` or raises
            `KeyError` for these), or the archive itself is not readable.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            member_name = _member_matching_edition(tar.getnames(), edition)
            if member_name is None:
                raise _ArchiveError("no .mmdb in archive")
            member = tar.getmember(member_name)
            if not member.isfile():
                raise _ArchiveError("bad archive")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise _ArchiveError("bad archive")
            data = extracted.read()
    except (KeyError, tarfile.TarError) as exc:
        raise _ArchiveError("bad archive") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{edition}.mmdb"
    dest.write_bytes(data)
    return len(data)


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Download `EDITIONS` (or the `--edition`-filtered subset) into `--out-dir`.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        transport: An `httpx.BaseTransport` to route requests through instead of the real
            network — the seam tests inject `httpx.MockTransport` through.

    Returns:
        `0` once every requested edition is downloaded and extracted; `1` on a missing key, a
        download failure, or a bad/mismatched archive. `SystemExit(2)` (argparse's own usage-error
        convention) on an unknown `--edition`.
    """
    parser = argparse.ArgumentParser(prog="fetch_geoip.py")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--edition", action="append", choices=EDITIONS, dest="editions")
    args = parser.parse_args(argv)

    editions = args.editions if args.editions else list(EDITIONS)
    out_dir = Path(args.out_dir)

    key = Settings().maxmind_license_key.get_secret_value()
    if not key:
        print("error: config_error: MAXMIND_LICENSE_KEY is not set", file=sys.stderr)
        return 1

    # httpx/httpcore log the full request URL (query params included) at INFO by default — that
    # would leak the key into any log handler. Quiet them before the first request, and restore
    # whatever level they had on the way out (review M5): `main()` runs in-process in every test
    # in this file, so an unrestored mutation would leak across the whole pytest session.
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    previous_httpx_level = httpx_logger.level
    previous_httpcore_level = httpcore_logger.level
    httpx_logger.setLevel(logging.WARNING)
    httpcore_logger.setLevel(logging.WARNING)

    try:
        with httpx.Client(transport=transport, timeout=60, follow_redirects=True) as client:
            for edition in editions:
                try:
                    response = client.get(
                        DOWNLOAD_URL,
                        params={"edition_id": edition, "license_key": key, "suffix": "tar.gz"},
                    )
                except httpx.HTTPError as exc:
                    print(
                        f"error: download_failed: {edition}: {type(exc).__name__}",
                        file=sys.stderr,
                    )
                    return 1

                if not (200 <= response.status_code < 300):
                    print(
                        f"error: download_failed: {edition}: HTTP {response.status_code}",
                        file=sys.stderr,
                    )
                    return 1

                try:
                    size = _extract_mmdb(response.content, edition, out_dir)
                except _ArchiveError as exc:
                    print(f"error: download_failed: {edition}: {exc}", file=sys.stderr)
                    return 1

                print(f"{edition}: {size} bytes -> {out_dir / f'{edition}.mmdb'}")

        return 0
    finally:
        httpx_logger.setLevel(previous_httpx_level)
        httpcore_logger.setLevel(previous_httpcore_level)


if __name__ == "__main__":
    raise SystemExit(main())
