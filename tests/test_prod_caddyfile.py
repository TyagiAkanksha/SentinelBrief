"""Pins `infra/deploy/prod/Caddyfile` against the m6 task-03 brief's Interfaces block: two TLS
site blocks, the `X-Forwarded-For` OVERWRITE (never append) that `FORWARDED_ALLOW_IPS=*` on the
`api` container depends on (PRD §10.10, `.claude/rules/infra.md`), the request body cap that is
the outer, on-the-wire bound of the api's own `INGEST_MAX_BODY_BYTES` (task-02), unbuffered
streaming for the future SSE route (PRD §8, M8), security headers on both vhosts, and no plaintext
site.

Pure text pins — no `caddy validate` here (that needs the `caddy:2` image and is the
implementer's GREEN-step evidence, per the brief's Steps).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CADDYFILE = _REPO_ROOT / "infra" / "deploy" / "prod" / "Caddyfile"

_API_HOST = "api.sentinelbrief.tyagiakanksha.com"
_WEB_HOST = "sentinelbrief.tyagiakanksha.com"


def _read_caddyfile() -> str:
    assert _CADDYFILE.exists(), (
        f"{_CADDYFILE} does not exist yet — task-03's GREEN step creates it per the brief's "
        "Interfaces block."
    )
    return _CADDYFILE.read_text()


def _extract_site_block(text: str, host: str) -> str:
    """Returns the text between `<host> {` and its matching top-level `}`, tracking brace depth so
    the nested `header {}` / `request_body {}` / `reverse_proxy ... {}` blocks inside a site don't
    end the extraction early.
    """
    marker = f"{host} {{"
    start = text.index(marker)
    depth = 0
    for i in range(start, len(text)):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"unterminated site block for host {host!r} in {_CADDYFILE}")


def test_two_hosts_xff_overwrite_body_cap_and_flush() -> None:
    """Brief Interfaces: both hostnames present; the `api` vhost overwrites `X-Forwarded-For`
    (never the append form, `{http.request.header.X-Forwarded-For}` — PRD §10.10), caps the
    request body at 2 MB (task-02's `INGEST_MAX_BODY_BYTES` outer bound), streams unbuffered
    (`flush_interval -1`, for M8's SSE route) and proxies to `api:8000`; the `web` vhost proxies to
    `web:3000`; both vhosts carry `Strict-Transport-Security`; no line anywhere serves plaintext
    HTTP.
    """
    text = _read_caddyfile()

    assert _API_HOST in text, text
    assert _WEB_HOST in text, text

    api_block = _extract_site_block(text, _API_HOST)
    web_block = _extract_site_block(text, _WEB_HOST)

    assert "header_up X-Forwarded-For {remote_host}" in api_block, api_block
    assert "max_size 2MB" in api_block, api_block
    assert "flush_interval -1" in api_block, api_block
    assert "reverse_proxy api:8000" in api_block, api_block

    assert "reverse_proxy web:3000" in web_block, web_block

    assert "Strict-Transport-Security" in api_block, api_block
    assert "Strict-Transport-Security" in web_block, web_block

    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("http://"), f"plaintext line in Caddyfile: {line!r}"
        assert not stripped.startswith(":80"), f"plaintext (port-80-only) site block: {line!r}"

    # OVERWRITE, never append: the append form re-adds a client-supplied XFF header alongside the
    # real one instead of replacing it, which would let an internet client spoof its own source IP
    # straight through to `FORWARDED_ALLOW_IPS=*` (PRD §10.10).
    assert text.count("header_up X-Forwarded-For") == 1, text
    assert "{http.request.header.X-Forwarded-For}" not in text, text
