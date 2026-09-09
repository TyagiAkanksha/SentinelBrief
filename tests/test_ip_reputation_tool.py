"""Pins `worker.tools.ip_reputation.IpReputationTool`: AbuseIPDB `check` reputation lookup behind
a 24 h `TTLCache`, with the key sent only in the `Key` header and every failure mapped to one of
eight typed `unavailable` reasons — never a raise, never a cached failure (PRD §6.3, m4 task-04).

Every unit test below builds the tool over `httpx.AsyncClient(transport=httpx.MockTransport(...))`
(CONVENTIONS.md §10: AbuseIPDB is the external seam, the only thing ever mocked) and an
`InMemoryTTLCache` (`core.cache`, m3 task-02) — the same `TTLCache` Protocol `RedisTTLCache`
implements at M5. `RecordingCache` below implements the `TTLCache` Protocol and delegates storage
to `InMemoryTTLCache`; it is a real implementation of our own seam, not a mock of our code
(CONVENTIONS.md §10), used only where a test needs to observe the exact `set(key, value, ttl_s)`
call the tool made (controller ruling R4).

`test_recorded_fixtures_replay_for_the_five_fixture_ips` exercises the five synthetic reputation
fixtures (task-04's own deliverable, written by the implementer via `write_fixture`) through
`ReplayToolRecorder`, the same seam the triage loop and the seed script use — never a live call.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from core.cache import InMemoryTTLCache, TTLCache
from core.config import Settings
from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import ReplayToolRecorder, ToolContext, fixture_path
from worker.tools.ip_reputation import ABUSEIPDB_CHECK_URL, CACHE_KEY_PREFIX, IpReputationTool

_FIXTURES_ROOT = Path("tests/fixtures/tools")
_TEST_KEY = "test-key"


class RecordingCache:
    """A `TTLCache` (`core.cache`) that logs every `set` call and delegates storage to a real
    `InMemoryTTLCache` — it implements our Protocol, it is not a mock of our own code
    (CONVENTIONS.md §10). Used to observe the exact `(key, value, ttl_s)` a tool passed to `set`.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._inner = InMemoryTTLCache() if clock is None else InMemoryTTLCache(clock=clock)
        self.set_calls: list[tuple[str, bytes, int]] = []

    async def get(self, key: str) -> bytes | None:
        return await self._inner.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self.set_calls.append((key, value, ttl_s))
        await self._inner.set(key, value, ttl_s)


class _CorruptCache:
    """A `TTLCache` stub whose `get` always returns the same non-JSON-object bytes — standing in
    for a corrupt cache entry (a truncated Redis RDB, or a stale entry from a different result
    schema once M5 shares the keyspace with the API cache, controller ruling I2 m4 task-04 fix-1)
    without needing a real Redis. `set` is recorded so a test can also assert the fresh result
    overwrites the poisoned entry.
    """

    def __init__(self, corrupt_value: bytes) -> None:
        self._corrupt_value = corrupt_value
        self.set_calls: list[tuple[str, bytes, int]] = []

    async def get(self, key: str) -> bytes | None:
        return self._corrupt_value

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self.set_calls.append((key, value, ttl_s))


def _success_body(
    score: int, reports: int, last_seen: str | None, *, ip: str = "203.0.113.10"
) -> dict[str, Any]:
    """A minimally valid AbuseIPDB `check` response body around the given fields."""
    return {
        "data": {
            "ipAddress": ip,
            "abuseConfidenceScore": score,
            "totalReports": reports,
            "lastReportedAt": last_seen,
        }
    }


def _make_alert(ip: str = "203.0.113.10") -> SessionAlert:
    """A minimal, valid `SessionAlert`: one connect event, no fixture/DB needed."""
    return SessionAlert(
        source="cowrie",
        session_id="s1",
        src_ip=ip,
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session="s1",
                src_ip=ip,
                sensor="sensor-1",
            )
        ],
    )


def _ctx(ip: str = "203.0.113.10") -> ToolContext:
    return ToolContext(alert=_make_alert(ip), session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


def _tool(
    *,
    api_key: str = _TEST_KEY,
    handler: Callable[[httpx.Request], httpx.Response],
    cache: TTLCache | None = None,
    cache_ttl_s: int = 86400,
    max_age_days: int = 90,
) -> IpReputationTool:
    return IpReputationTool(
        api_key=api_key,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=cache if cache is not None else InMemoryTTLCache(),
        cache_ttl_s=cache_ttl_s,
        max_age_days=max_age_days,
    )


def _never_called_handler(request: httpx.Request) -> httpx.Response:
    pytest.fail(f"unexpected request: {request.method} {request.url}")


async def test_sends_key_header_accept_json_and_query_params() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    tool = _tool(handler=handler)

    await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url).split("?")[0] == ABUSEIPDB_CHECK_URL
    assert request.headers["Key"] == "test-key"
    assert request.headers["Accept"] == "application/json"
    assert dict(request.url.params) == {"ipAddress": "203.0.113.10", "maxAgeInDays": "90"}


async def test_maps_data_fields_to_abuse_score_reports_last_seen() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_success_body(87, 96, "2026-09-04T03:41:19+00:00", ip="198.51.100.23"),
        )

    tool = _tool(handler=handler)

    result = await tool.run({"ip": "198.51.100.23"}, _ctx("198.51.100.23"))

    assert result == {
        "ip": "198.51.100.23",
        "abuse_score": 87,
        "reports": 96,
        "last_seen": "2026-09-04T03:41:19+00:00",
        "cached": False,
    }

    def null_last_seen_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_body(0, 0, None, ip="198.51.100.23"))

    tool2 = _tool(handler=null_last_seen_handler)

    result2 = await tool2.run({"ip": "198.51.100.23"}, _ctx("198.51.100.23"))

    assert result2["last_seen"] is None


async def test_second_lookup_within_ttl_is_served_from_cache() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    box = [0.0]
    tool = _tool(handler=handler, cache=InMemoryTTLCache(clock=lambda: box[0]))

    first = await tool.run({"ip": "203.0.113.10"}, _ctx())
    box[0] = 100.0
    second = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert request_count == 1
    assert first["cached"] is False
    assert second == {**first, "cached": True}


async def test_lookup_after_ttl_hits_the_network_again() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    box = [0.0]
    cache = RecordingCache(clock=lambda: box[0])
    tool = _tool(handler=handler, cache=cache, cache_ttl_s=86400)

    await tool.run({"ip": "203.0.113.10"}, _ctx())
    box[0] = 86401.0  # strictly past the 86400 s TTL (core/cache.py's expiry uses `>=`)
    await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert request_count == 2
    assert len(cache.set_calls) == 2
    assert all(ttl_s == 86400 for _key, _value, ttl_s in cache.set_calls)


async def test_cache_key_is_prefixed_with_the_ip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    cache = RecordingCache()
    tool = _tool(handler=handler, cache=cache)

    await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert len(cache.set_calls) == 1
    assert cache.set_calls[0][0] == "abuseipdb:203.0.113.10"
    assert cache.set_calls[0][0] == CACHE_KEY_PREFIX + "203.0.113.10"


async def test_failures_are_never_cached() -> None:
    responses = [
        httpx.Response(429),
        httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00")),
    ]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    cache = RecordingCache()
    tool = _tool(handler=handler, cache=cache)

    first = await tool.run({"ip": "203.0.113.10"}, _ctx())
    assert len(cache.set_calls) == 0  # the 429 must never be cached

    second = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert len(calls) == 2
    assert first == {"unavailable": True, "reason": "quota_exceeded"}
    assert second["cached"] is False
    assert second["abuse_score"] == 100
    assert len(cache.set_calls) == 1  # only the success was ever cached


async def test_no_api_key_is_unavailable_and_logs_once_without_network(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    tool = _tool(api_key="", handler=handler)

    with caplog.at_level(logging.WARNING):
        first = await tool.run({"ip": "203.0.113.10"}, _ctx())
        second = await tool.run({"ip": "198.51.100.23"}, _ctx("198.51.100.23"))

    assert first == {"unavailable": True, "reason": "no_api_key"}
    assert second == {"unavailable": True, "reason": "no_api_key"}
    assert calls == []
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


async def test_429_is_quota_exceeded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    tool = _tool(handler=handler)

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result == {"unavailable": True, "reason": "quota_exceeded"}


async def test_401_and_403_are_unauthorized() -> None:
    for status in (401, 403):

        def handler(request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status)

        tool = _tool(handler=handler)

        result = await tool.run({"ip": "203.0.113.10"}, _ctx())

        assert result == {"unavailable": True, "reason": "unauthorized"}


async def test_other_non_2xx_is_http_status() -> None:
    for status in (500, 422):

        def handler(request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status)

        tool = _tool(handler=handler)

        result = await tool.run({"ip": "203.0.113.10"}, _ctx())

        assert result == {"unavailable": True, "reason": f"http_{status}"}


async def test_transport_error_and_timeout_are_network_error() -> None:
    for exc in (httpx.ConnectError("boom"), httpx.ReadTimeout("boom")):

        def handler(request: httpx.Request, exc: httpx.HTTPError = exc) -> httpx.Response:
            raise exc

        tool = _tool(handler=handler)

        result = await tool.run({"ip": "203.0.113.10"}, _ctx())

        assert result == {"unavailable": True, "reason": "network_error"}


async def test_malformed_bodies_are_malformed_response() -> None:
    bodies = [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": {"abuseConfidenceScore": "high"}}),
        # Controller ruling (m4 task-04 fix-1, finding M2): `last_seen` is `str | None` by
        # contract (brief Interfaces line 85) — any other JSON type (here, an epoch int) is a
        # malformed body, not a value silently passed through and cached.
        httpx.Response(
            200,
            json={
                "data": {
                    "abuseConfidenceScore": 5,
                    "totalReports": 1,
                    "lastReportedAt": 1757000000,
                }
            },
        ),
    ]

    for body in bodies:

        def handler(request: httpx.Request, body: httpx.Response = body) -> httpx.Response:
            return body

        tool = _tool(handler=handler)

        result = await tool.run({"ip": "203.0.113.10"}, _ctx())

        assert result == {"unavailable": True, "reason": "malformed_response"}


async def test_invalid_ip_is_invalid_arguments_before_any_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    tool = _tool(handler=handler)
    ctx = _ctx()

    assert await tool.run({"ip": "nope"}, ctx) == {
        "unavailable": True,
        "reason": "invalid_arguments",
    }
    assert await tool.run({}, ctx) == {"unavailable": True, "reason": "invalid_arguments"}
    assert await tool.run({"ip": 1}, ctx) == {"unavailable": True, "reason": "invalid_arguments"}
    assert calls == []

    # Controller ruling (m4 task-04 fix-1, finding I1): the ordering ("invalid ip before key",
    # brief Interfaces line 78) is only observable when the key is unconfigured — the
    # shipped-unkeyed configuration (`.env.example` ABUSEIPDB_API_KEY= empty, PRD §13) is the only
    # one where "invalid ip before key" and "key before ip" diverge in their `reason` string.
    unkeyed_calls: list[httpx.Request] = []

    def unkeyed_handler(request: httpx.Request) -> httpx.Response:
        unkeyed_calls.append(request)
        return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

    unkeyed_tool = _tool(api_key="", handler=unkeyed_handler)

    with caplog.at_level(logging.WARNING):
        unkeyed_result = await unkeyed_tool.run({"ip": "nope"}, ctx)

    assert unkeyed_result == {"unavailable": True, "reason": "invalid_arguments"}
    assert unkeyed_calls == []
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


async def test_key_never_appears_in_results_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Controller ruling (m4 task-04 fix-1, finding C1): three DISTINCT documentation IPs, so
    each scenario's request actually reaches the transport instead of being served from a
    previous call's cache entry — the earlier, same-IP version of this test only ever exercised
    its own success branch (calls 2 and 3 were served from the cache call 1 wrote) and was
    therefore vacuous on exactly the branches ("their text is not echoed", brief Interfaces line
    90) it exists to pin.
    """
    scripted: list[httpx.Response | Exception] = [
        httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00")),
        httpx.Response(429),
        httpx.ConnectError("connection refused (Key test-key rejected upstream)"),
    ]
    invocations: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        invocations.append(request)
        next_item = scripted.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    tool = _tool(handler=handler)

    caplog.set_level(logging.DEBUG)
    success = await tool.run({"ip": "203.0.113.10"}, _ctx())
    quota = await tool.run({"ip": "198.51.100.23"}, _ctx("198.51.100.23"))
    network = await tool.run({"ip": "192.0.2.55"}, _ctx("192.0.2.55"))

    # The short-circuit-back-to-cache regression this test exists to catch: with three distinct
    # IPs, every one of the three scripted transport outcomes must actually have been consumed.
    assert len(invocations) == 3
    assert success["cached"] is False
    assert quota == {"unavailable": True, "reason": "quota_exceeded"}
    assert network == {"unavailable": True, "reason": "network_error"}

    assert "test-key" not in json.dumps(success)
    assert "test-key" not in json.dumps(quota)
    assert "test-key" not in json.dumps(network)
    assert "test-key" not in caplog.text


async def test_corrupt_cache_entry_is_treated_as_a_miss() -> None:
    """Controller ruling (m4 task-04 fix-1, finding I2): a cache hit that is not a JSON object —
    a corrupt/truncated entry, or a stale entry from a different result schema once M5 shares the
    Redis keyspace with the API cache — must be treated as a miss, never raise out of `run`
    (`worker/tools/base.py`'s "NEVER raises by contract"; brief Interfaces line 89 "Never
    raises"). Two distinct corrupt shapes: bytes that are not valid JSON at all, and bytes that
    parse to valid JSON but not a JSON *object* (a `dict`).
    """
    for corrupt_value in (b"not json", b"[1]"):
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00"))

        tool = _tool(handler=handler, cache=_CorruptCache(corrupt_value))

        result = await tool.run({"ip": "203.0.113.10"}, _ctx())

        assert request_count == 1  # the corrupt hit was never served; the live lookup ran
        assert result == {
            "ip": "203.0.113.10",
            "abuse_score": 100,
            "reports": 412,
            "last_seen": "2026-09-05T22:14:03+00:00",
            "cached": False,
        }


async def test_ipv6_spellings_share_one_cache_entry_and_canonical_ip() -> None:
    """Controller ruling (m4 task-04 fix-1, finding M3): two spellings of the same IPv6 address
    must canonicalize to one cache entry and spend AbuseIPDB quota once, not once per spelling —
    the free tier is 1 000 checks/day (PRD §6.3) and this tool's whole purpose is spending it once
    per IP per day (brief Interfaces line 68).
    """
    request_count = 0
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        captured.append(request)
        return httpx.Response(
            200, json=_success_body(23, 4, "2026-08-30T11:07:55+00:00", ip="2001:db8::1")
        )

    cache = RecordingCache()
    tool = _tool(handler=handler, cache=cache)

    first = await tool.run({"ip": "2001:DB8::1"}, _ctx())
    second = await tool.run({"ip": "2001:0db8:0000:0000:0000:0000:0000:0001"}, _ctx())

    assert request_count == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["ip"] == "2001:db8::1"
    assert second["ip"] == "2001:db8::1"
    assert cache.set_calls[0][0] == "abuseipdb:2001:db8::1"
    assert dict(captured[0].url.params)["ipAddress"] == "2001:db8::1"


def test_rejects_nonpositive_ttl_and_max_age() -> None:
    with pytest.raises(ValueError):
        _tool(handler=_never_called_handler, cache_ttl_s=0)
    with pytest.raises(ValueError):
        _tool(handler=_never_called_handler, max_age_days=0)


def test_tool_is_external() -> None:
    tool = _tool(api_key="", handler=_never_called_handler)

    assert tool.external is True


def test_abuseipdb_settings_defaults_bounds_and_secret_repr() -> None:
    settings = Settings()

    assert settings.abuseipdb_cache_ttl_s == 86400
    assert settings.abuseipdb_timeout_s == 5.0
    assert settings.abuseipdb_max_age_days == 90
    assert settings.abuseipdb_cache_max_entries == 4096
    assert settings.abuseipdb_api_key.get_secret_value() == ""

    with pytest.raises(ValidationError):
        Settings(abuseipdb_max_age_days=366)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(abuseipdb_timeout_s=0)  # type: ignore[call-arg]
    # Controller ruling (m4 task-04 fix-1, finding M4): the other two numeric bounds
    # (`core/config.py`'s `ge=1` on each) were previously untested.
    with pytest.raises(ValidationError):
        Settings(abuseipdb_cache_ttl_s=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(abuseipdb_cache_max_entries=0)  # type: ignore[call-arg]

    secret_settings = Settings(abuseipdb_api_key=SecretStr("abuse-key-1"))
    assert "abuse-key-1" not in repr(secret_settings)


async def test_recorded_fixtures_replay_for_the_five_fixture_ips() -> None:
    recorder = ReplayToolRecorder(_FIXTURES_ROOT)
    tool = _tool(api_key=_TEST_KEY, handler=_never_called_handler)  # never called: replay only
    ctx = _ctx()

    expected_scores = {
        "203.0.113.10": 100,
        "198.51.100.23": 87,
        "203.0.113.77": 23,
        "192.0.2.55": 64,
        "198.51.100.140": 100,
    }
    # Controller ruling (m4 task-04 fix-1, finding M1): the brief's five literal fixture file
    # names — `assert path.name == f"{fixture_key(arguments)}.json"` cannot fail, since
    # `fixture_path` is *defined* as exactly that expression (`worker/tools/recorder.py`).
    expected_file_names = {
        "5d2e7bda8feb939e.json",
        "6a624fe81e1c51a3.json",
        "1ba86fc94704aedc.json",
        "70c94a209a3ec9bd.json",
        "55230db792e5f6bf.json",
    }

    seen_file_names: set[str] = set()
    for ip, expected_score in expected_scores.items():
        arguments = {"ip": ip}

        result = await recorder.execute(tool, arguments, ctx)

        assert result["ip"] == ip
        assert result["abuse_score"] == expected_score
        assert "cached" not in result

        path = fixture_path(_FIXTURES_ROOT, tool.name, arguments)
        assert path.exists()
        seen_file_names.add(path.name)

    assert seen_file_names == expected_file_names
