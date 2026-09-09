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

from core.cache import InMemoryTTLCache
from core.config import Settings
from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import ReplayToolRecorder, ToolContext, fixture_key, fixture_path
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
    cache: InMemoryTTLCache | RecordingCache | None = None,
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
    ]

    for body in bodies:

        def handler(request: httpx.Request, body: httpx.Response = body) -> httpx.Response:
            return body

        tool = _tool(handler=handler)

        result = await tool.run({"ip": "203.0.113.10"}, _ctx())

        assert result == {"unavailable": True, "reason": "malformed_response"}


async def test_invalid_ip_is_invalid_arguments_before_any_request() -> None:
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


async def test_key_never_appears_in_results_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    scripted: list[httpx.Response | Exception] = [
        httpx.Response(200, json=_success_body(100, 412, "2026-09-05T22:14:03+00:00")),
        httpx.Response(429),
        httpx.ConnectError("connection refused (Key test-key rejected upstream)"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        next_item = scripted.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    tool = _tool(handler=handler)

    caplog.set_level(logging.DEBUG)
    success = await tool.run({"ip": "203.0.113.10"}, _ctx())
    quota = await tool.run({"ip": "203.0.113.10"}, _ctx())
    network = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert "test-key" not in json.dumps(success)
    assert "test-key" not in json.dumps(quota)
    assert "test-key" not in json.dumps(network)
    assert "test-key" not in caplog.text


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

    for ip, expected_score in expected_scores.items():
        arguments = {"ip": ip}

        result = await recorder.execute(tool, arguments, ctx)

        assert result["ip"] == ip
        assert result["abuse_score"] == expected_score
        assert "cached" not in result

        path = fixture_path(_FIXTURES_ROOT, tool.name, arguments)
        assert path.name == f"{fixture_key(arguments)}.json"
