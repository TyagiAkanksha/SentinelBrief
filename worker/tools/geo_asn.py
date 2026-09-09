"""`get_ip_geo_asn` over local MaxMind GeoLite2 databases (PRD §6.3, §10.8; m4 task-03).

`GeoAsnTool` answers `{ip, country, asn, org}` from two readers opened once at construction — a
GeoLite2 Country (or City) database for `country` and a GeoLite2 ASN database for `asn`/`org`.
Either reader missing/unreadable nulls only its half of the answer; both missing is
`unavailable("geoip_db_not_configured")`, logged once per instance. An address the database has
never heard of is a successful lookup with null fields, not `unavailable`. The tool never raises:
a malformed `ip` argument or a reader's `ValueError` on it is `invalid_arguments`; a corrupt
database discovered at lookup time (`maxminddb.InvalidDatabaseError`) is `geoip_db_error`.

This is the ONLY module that imports `maxminddb` — the `.mmdb` file is an external seam
(CONVENTIONS.md §10) and no `.mmdb` may ever be committed, so unit tests drive an in-memory
`GeoReader` fake instead (`tests/test_geo_asn_tool.py`); the five real fixtures for the seed's
replay live under `tests/fixtures/tools/get_ip_geo_asn/`.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from typing import Any, Protocol, cast

import maxminddb

from core.config import Settings
from worker.tools.base import ToolContext, unavailable

logger = logging.getLogger(__name__)


class GeoReader(Protocol):
    """A one-method seam over `maxminddb.Reader` so unit tests use an in-memory fake."""

    def get(self, ip: str) -> Mapping[str, Any] | None:
        """Look up `ip`; `None` when the database has no entry for it.

        Mirrors `maxminddb.Reader.get`'s own contract: raises `ValueError` on a malformed
        address and `maxminddb.InvalidDatabaseError` on a corrupt database discovered at lookup
        time.
        """
        ...


def open_reader(path: str) -> GeoReader | None:
    """Open the `.mmdb` at `path`, or `None` when it is not configured or unreadable.

    Args:
        path: The `.mmdb` file path; `""` means "not configured".

    Returns:
        A `GeoReader`, or `None` when `path` is empty, missing, or corrupt. `OSError` and
        `maxminddb.InvalidDatabaseError` are logged once (the path only, never file contents)
        and degrade to `None` rather than raising.
    """
    if not path:
        return None
    try:
        # maxminddb.Reader.get's declared return type (`Record | None`, a broader union than
        # `Mapping[str, Any] | None`) is structurally wider than `GeoReader` but a country/ASN
        # database's records are always dicts at runtime — cast to the seam's own contract.
        return cast(GeoReader, maxminddb.open_database(path))
    except (OSError, maxminddb.InvalidDatabaseError):
        logger.warning("geoip database unreadable path=%s", path)
        return None


class GeoAsnTool:
    """`get_ip_geo_asn(ip) -> {ip, country, asn, org}` (PRD §6.3)."""

    name = "get_ip_geo_asn"
    external = True  # depends on a downloaded DB -> replayed from fixtures in evals/tests
    description = (
        "Look up the country, autonomous system number and organisation of an IP address from a "
        "local GeoLite2 database (no network)."
    )
    parameters = {
        "type": "object",
        "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."}},
        "required": ["ip"],
        "additionalProperties": False,
    }

    def __init__(self, *, country: GeoReader | None, asn: GeoReader | None) -> None:
        """Build the tool over already-opened readers.

        Args:
            country: A GeoLite2 Country (or City) reader, or `None` when not configured.
            asn: A GeoLite2 ASN reader, or `None` when not configured.
        """
        self._country = country
        self._asn = asn
        self._warned_not_configured = False

    @classmethod
    def from_settings(cls, settings: Settings) -> GeoAsnTool:
        """Build the tool by opening both `Settings` paths (each may fail independently)."""
        return cls(
            country=open_reader(settings.geoip_db_path),
            asn=open_reader(settings.geoip_asn_db_path),
        )

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Answer `{ip, country, asn, org}`; never raises (the "tools never raise" contract)."""
        ip = arguments.get("ip")
        if not isinstance(ip, str):
            return unavailable("invalid_arguments")
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return unavailable("invalid_arguments")

        if self._country is None and self._asn is None:
            if not self._warned_not_configured:
                logger.warning("get_ip_geo_asn called with no geoip database configured")
                self._warned_not_configured = True
            return unavailable("geoip_db_not_configured")

        try:
            country = self._lookup_country(ip)
            asn, org = self._lookup_asn(ip)
        except ValueError:
            return unavailable("invalid_arguments")
        except maxminddb.InvalidDatabaseError:
            return unavailable("geoip_db_error")

        return {"ip": ip, "country": country, "asn": asn, "org": org}

    def _lookup_country(self, ip: str) -> str | None:
        if self._country is None:
            return None
        record = self._country.get(ip)
        if record is None:
            return None
        country = record.get("country")
        if not isinstance(country, Mapping):
            return None
        iso_code = country.get("iso_code")
        return iso_code if isinstance(iso_code, str) else None

    def _lookup_asn(self, ip: str) -> tuple[int | None, str | None]:
        if self._asn is None:
            return None, None
        record = self._asn.get(ip)
        if record is None:
            return None, None
        asn = record.get("autonomous_system_number")
        org = record.get("autonomous_system_organization")
        return (
            asn if isinstance(asn, int) else None,
            org if isinstance(org, str) else None,
        )
