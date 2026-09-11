"""`lookup_ip_reputation` over AbuseIPDB's `check` endpoint (PRD §6.3, §10.6, §13).

`IpReputationTool` calls AbuseIPDB's free-tier `check` endpoint through an injected
`httpx.AsyncClient` and answers `{ip, abuse_score, reports, last_seen, cached}`. Successful
answers are cached for `ABUSEIPDB_CACHE_TTL_S` (PRD §6.3: 24 h) behind the M3 `core.cache.TTLCache`
Protocol so the free tier's daily quota is spent once per IP per day; a failure is never cached.
The AbuseIPDB account is the owner's own (PRD §13) — until `ABUSEIPDB_API_KEY` is set this tool
always answers `unavailable("no_api_key")`, logged once per instance.

Account-wide quota back-off (m5 task-05): AbuseIPDB's free tier is a single daily quota shared by
every IP looked up, not a per-IP limit. A `429` therefore sets ONE negative cache key (`QUOTA_KEY`,
no IP in it) for `quota_backoff_s` seconds; while that key exists, every lookup for any IP answers
`unavailable("quota_exceeded")` without making an HTTP call, so a single `429` cannot burn through
the rest of the day's calls one rejected IP at a time. `quota_backoff_s=0` disables the back-off
entirely (each `429` is independent, as before this task). The per-ip success key is never written
on any failure path, including this one.

The tool never raises (the "tools never raise" contract, `worker/tools/base.py`): a bad address,
a missing key, a quota/auth/other HTTP error, a transport failure, or a malformed response body
are all typed `unavailable(reason)`. The API key travels in the `Key` header only and is never
echoed into a result, a log line, or an exception message this module produces.

This is the ONLY module that talks to `abuseipdb.com` — the network is an external seam
(CONVENTIONS.md §10), so unit tests drive the tool through `httpx.MockTransport`
(`tests/test_ip_reputation_tool.py`); the real endpoint is touched only by the opt-in
`@pytest.mark.live` smoke (`tests/test_ip_reputation_live.py`). The five synthetic reputation
fixtures for the seed's fixture IPs live under `tests/fixtures/tools/lookup_ip_reputation/`.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from core.cache import TTLCache
from worker.tools.base import ToolContext, unavailable

logger = logging.getLogger(__name__)

ABUSEIPDB_CHECK_URL = "https://api.abuseipdb.com/api/v2/check"
CACHE_KEY_PREFIX = "abuseipdb:"
QUOTA_KEY = CACHE_KEY_PREFIX + "quota_exceeded"
"""Account-wide negative-cache key (m5 task-05): set for `quota_backoff_s` seconds after a `429`,
no IP in it — the free tier's quota is shared across every IP, not per-address."""


class IpReputationTool:
    """`lookup_ip_reputation(ip) -> {ip, abuse_score, reports, last_seen, cached}` (PRD §6.3)."""

    name = "lookup_ip_reputation"
    external = True  # depends on a live vendor API -> replayed from fixtures in evals/tests
    description = (
        "Look up an IP address's abuse reputation (AbuseIPDB confidence score 0-100, report "
        "count, last report time). Costs quota; call it once per address."
    )
    parameters = {
        "type": "object",
        "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."}},
        "required": ["ip"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        api_key: str,
        http: httpx.AsyncClient,
        cache: TTLCache,
        cache_ttl_s: int,
        max_age_days: int,
        quota_backoff_s: int = 0,
    ) -> None:
        """Build the tool over an already-configured HTTP client and cache.

        Args:
            api_key: The AbuseIPDB key sent in the `Key` header; `""` means "not configured".
            http: The client to issue the `check` request through; carries the timeout (built by
                the wiring layer from `ABUSEIPDB_TIMEOUT_S`).
            cache: Where successful answers are cached, keyed by `CACHE_KEY_PREFIX + ip`; also
                where the account-wide `QUOTA_KEY` back-off flag is set/read (m5 task-05).
            cache_ttl_s: Seconds a successful answer is cached for; must be >= 1.
            max_age_days: `maxAgeInDays` sent to AbuseIPDB; must be >= 1.
            quota_backoff_s: Seconds to skip AbuseIPDB entirely after a `429` (m5 task-05); `0`
                disables the back-off. Must be >= 0.

        Raises:
            ValueError: When `cache_ttl_s` or `max_age_days` is not positive, or `quota_backoff_s`
                is negative.
        """
        if cache_ttl_s < 1:
            raise ValueError("cache_ttl_s must be >= 1")
        if max_age_days < 1:
            raise ValueError("max_age_days must be >= 1")
        if quota_backoff_s < 0:
            raise ValueError("quota_backoff_s must be >= 0")
        self._api_key = api_key
        self._http = http
        self._cache = cache
        self._cache_ttl_s = cache_ttl_s
        self._max_age_days = max_age_days
        self._quota_backoff_s = quota_backoff_s
        self._warned_no_api_key = False

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Answer `{ip, abuse_score, reports, last_seen, cached}`; never raises."""
        ip = arguments.get("ip")
        if not isinstance(ip, str):
            return unavailable("invalid_arguments")
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            return unavailable("invalid_arguments")

        if self._api_key == "":
            if not self._warned_no_api_key:
                logger.warning("lookup_ip_reputation called with no ABUSEIPDB_API_KEY configured")
                self._warned_no_api_key = True
            return unavailable("no_api_key")

        if self._quota_backoff_s > 0 and await self._cache.get(QUOTA_KEY) is not None:
            return unavailable("quota_exceeded")

        cache_key = CACHE_KEY_PREFIX + ip
        cached = await self._cache.get(cache_key)
        if cached is not None:
            try:
                payload = json.loads(cached)
            except (ValueError, UnicodeDecodeError):
                payload = None
            if isinstance(payload, dict):
                return {**payload, "cached": True}
            logger.debug("lookup_ip_reputation cache hit was not a JSON object; treating as a miss")

        try:
            response = await self._http.get(
                ABUSEIPDB_CHECK_URL,
                params={"ipAddress": ip, "maxAgeInDays": str(self._max_age_days)},
                headers={"Key": self._api_key, "Accept": "application/json"},
            )
        except httpx.HTTPError:
            return unavailable("network_error")

        if response.status_code == 429:
            if self._quota_backoff_s > 0:
                await self._cache.set(QUOTA_KEY, b"1", self._quota_backoff_s)
            return unavailable("quota_exceeded")
        if response.status_code in (401, 403):
            return unavailable("unauthorized")
        if not (200 <= response.status_code < 300):
            return unavailable(f"http_{response.status_code}")

        try:
            data = response.json()["data"]
            if not isinstance(data, dict):
                raise TypeError("data is not an object")
            last_seen = data.get("lastReportedAt")
            if last_seen is not None and not isinstance(last_seen, str):
                raise TypeError
            result = {
                "ip": ip,
                "abuse_score": int(data["abuseConfidenceScore"]),
                "reports": int(data["totalReports"]),
                "last_seen": last_seen,
            }
        except (json.JSONDecodeError, KeyError, OverflowError, TypeError, ValueError):
            return unavailable("malformed_response")

        await self._cache.set(cache_key, json.dumps(result).encode(), self._cache_ttl_s)
        return {**result, "cached": False}
