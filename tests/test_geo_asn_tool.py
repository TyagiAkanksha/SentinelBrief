"""Pins `worker.tools.geo_asn`: `GeoAsnTool` answers `{ip, country, asn, org}` from two local
GeoLite2 readers (`GeoReader` Protocol) opened once at construction — the country reader supplies
`country`, the ASN reader supplies `asn`/`org`; either missing/unreadable nulls that half of the
answer, both missing is `unavailable("geoip_db_not_configured")` logged once, an unknown address
is nulls (not unavailability), and the tool never raises (PRD §6.3, m4 task-03).

The real `.mmdb` is an external seam (CONVENTIONS.md §10) — no `.mmdb` fixture file may ever be
committed (brief Goal), so every unit test below drives a `FakeGeoReader` in-memory fake instead:
`get` returns the mapped record, `None` for an ip with no entry, and raises `ValueError` when the
mapped value is the sentinel `"bad"` — standing in for `maxminddb.Reader.get`'s own `ValueError`
on a malformed address without ever touching a real database file.

`test_recorded_fixtures_replay_for_the_five_fixture_ips` exercises the five synthetic geo
fixtures (task-03's own deliverable) through `ReplayToolRecorder`, the same seam the triage loop
and the seed script (task-06) use.

Fix round 1 (review Findings I1/I2/M4): adds
`test_from_settings_wires_country_path_to_country_reader_and_asn_path_to_asn_reader` (I1 — the
prior `from_settings` test could not distinguish a swapped path→reader wiring), extends
`test_record_without_iso_code_gives_null_country` with a populated `registered_country` (I2 —
that field must never be a fallback), and extends
`test_recorded_fixtures_replay_for_the_five_fixture_ips` to assert each fixture's full result
body, not just `country` (M4).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import maxminddb
import pytest
from pydantic import SecretStr

from core.config import Settings
from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import ReplayToolRecorder, ToolContext, fixture_key, fixture_path, unavailable
from worker.tools.geo_asn import GeoAsnTool, open_reader

_FIXTURES_ROOT = Path("tests/fixtures/tools")


class FakeGeoReader:
    """An in-memory `GeoReader` fake — the `.mmdb` file is an external seam and no fixture file
    of one may ever be committed (brief Goal). `get` returns the record mapped to `ip`; an ip
    with no entry returns `None` (a lookup that found nothing, not a failure); an ip mapped to
    the sentinel value `"bad"` raises `ValueError`, standing in for `maxminddb.Reader.get`'s own
    contract of raising `ValueError` on a malformed address (Interfaces: `GeoReader.get`).
    """

    def __init__(self, records: dict[str, Any]) -> None:
        self._records = records

    def get(self, ip: str) -> Mapping[str, Any] | None:
        record = self._records.get(ip)
        if record == "bad":
            raise ValueError(f"malformed address: {ip}")
        return record


class _RaisingReader:
    """A `GeoReader` fake whose `get` always raises `maxminddb.InvalidDatabaseError` — simulates
    a corrupt `.mmdb` discovered at lookup time, distinct from `FakeGeoReader`'s `ValueError`
    path (Interfaces: a reader raising `InvalidDatabaseError` -> `unavailable("geoip_db_error")`).
    """

    def get(self, ip: str) -> Mapping[str, Any] | None:
        raise maxminddb.InvalidDatabaseError("corrupt database")


class _RecordingReader:
    """A `GeoReader` fake that records every `ip` it was called with and always returns `None` —
    used to pin that an invalid `ip` argument short-circuits before either reader is consulted.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, ip: str) -> Mapping[str, Any] | None:
        self.calls.append(ip)
        return None


def _make_alert() -> SessionAlert:
    """A minimal, valid `SessionAlert`: one connect event, no fixture/DB needed."""
    return SessionAlert(
        source="cowrie",
        session_id="s1",
        src_ip="203.0.113.10",
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session="s1",
                src_ip="203.0.113.10",
                sensor="sensor-1",
            )
        ],
    )


def _ctx() -> ToolContext:
    return ToolContext(alert=_make_alert(), session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


async def test_country_asn_org_from_both_readers() -> None:
    country = FakeGeoReader({"203.0.113.10": {"country": {"iso_code": "NL"}}})
    asn = FakeGeoReader(
        {
            "203.0.113.10": {
                "autonomous_system_number": 64496,
                "autonomous_system_organization": "Example",
            }
        }
    )
    tool = GeoAsnTool(country=country, asn=asn)

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result == {"ip": "203.0.113.10", "country": "NL", "asn": 64496, "org": "Example"}


async def test_asn_reader_missing_gives_null_asn_and_org() -> None:
    country = FakeGeoReader({"203.0.113.10": {"country": {"iso_code": "NL"}}})
    tool = GeoAsnTool(country=country, asn=None)

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result["country"] == "NL"
    assert result["asn"] is None
    assert result["org"] is None


async def test_country_reader_missing_gives_null_country() -> None:
    asn = FakeGeoReader(
        {
            "203.0.113.10": {
                "autonomous_system_number": 64496,
                "autonomous_system_organization": "Example",
            }
        }
    )
    tool = GeoAsnTool(country=None, asn=asn)

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result["country"] is None
    assert result["asn"] == 64496
    assert result["org"] == "Example"


async def test_no_readers_is_unavailable_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    tool = GeoAsnTool(country=None, asn=None)
    ctx = _ctx()

    with caplog.at_level(logging.WARNING):
        result_1 = await tool.run({"ip": "203.0.113.10"}, ctx)
        result_2 = await tool.run({"ip": "198.51.100.23"}, ctx)

    assert result_1 == unavailable("geoip_db_not_configured")
    assert result_2 == unavailable("geoip_db_not_configured")
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1


async def test_unknown_ip_gives_null_fields_not_unavailable() -> None:
    tool = GeoAsnTool(country=FakeGeoReader({}), asn=FakeGeoReader({}))

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result == {"ip": "203.0.113.10", "country": None, "asn": None, "org": None}


async def test_record_without_iso_code_gives_null_country() -> None:
    """Also pins that `registered_country` is never consulted as a fallback (Interfaces line 94,
    review Finding I2): the record here carries a *populated* `registered_country.iso_code` next
    to an empty `country`, so a `record["registered_country"]` fallback would answer `"RU"` where
    the correct, brief-mandated answer is `None`.
    """
    tool = GeoAsnTool(
        country=FakeGeoReader(
            {"203.0.113.10": {"country": {}, "registered_country": {"iso_code": "RU"}}}
        ),
        asn=None,
    )

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result["country"] is None


async def test_invalid_ip_is_invalid_arguments() -> None:
    reader = _RecordingReader()
    tool = GeoAsnTool(country=reader, asn=reader)
    ctx = _ctx()

    assert await tool.run({"ip": "not-an-ip"}, ctx) == unavailable("invalid_arguments")
    assert await tool.run({"ip": 7}, ctx) == unavailable("invalid_arguments")
    assert await tool.run({}, ctx) == unavailable("invalid_arguments")
    assert reader.calls == []


async def test_reader_value_error_is_invalid_arguments() -> None:
    country = FakeGeoReader({"203.0.113.10": "bad"})
    tool = GeoAsnTool(country=country, asn=None)

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result == unavailable("invalid_arguments")


async def test_invalid_database_error_is_geoip_db_error() -> None:
    tool = GeoAsnTool(country=_RaisingReader(), asn=None)

    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result == unavailable("geoip_db_error")


def test_open_reader_empty_missing_and_corrupt_paths_return_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert open_reader("") is None
    assert caplog.records == []

    caplog.clear()
    missing_path = tmp_path / "missing.mmdb"
    with caplog.at_level(logging.WARNING):
        assert open_reader(str(missing_path)) is None
    missing_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(missing_warnings) == 1
    assert str(missing_path) in missing_warnings[0].getMessage()

    caplog.clear()
    corrupt_path = tmp_path / "corrupt.mmdb"
    corrupt_path.write_bytes(b"\x00" * 16)
    with caplog.at_level(logging.WARNING):
        assert open_reader(str(corrupt_path)) is None
    corrupt_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(corrupt_warnings) == 1
    assert str(corrupt_path) in corrupt_warnings[0].getMessage()


async def test_from_settings_uses_both_paths() -> None:
    settings = Settings(geoip_db_path="", geoip_asn_db_path="")

    tool = GeoAsnTool.from_settings(settings)
    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert result == unavailable("geoip_db_not_configured")


async def test_from_settings_wires_country_path_to_country_reader_and_asn_path_to_asn_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review Finding I1: `Settings(geoip_db_path="", geoip_asn_db_path="")` (the only prior
    `from_settings` test) cannot distinguish which path feeds which reader — both inputs are
    identical. Monkeypatches `worker.tools.geo_asn.open_reader` with a recorder that both proves
    the call ORDER (`geoip_db_path` opened before `geoip_asn_db_path`, per Interfaces line 90)
    and returns a distinguishable fake per path, so a swap (`country` fed the ASN path and vice
    versa) is caught by the `run()` result, not just the call order.
    """
    calls: list[str] = []
    country_reader = FakeGeoReader({"203.0.113.10": {"country": {"iso_code": "NL"}}})
    asn_reader = FakeGeoReader(
        {
            "203.0.113.10": {
                "autonomous_system_number": 64496,
                "autonomous_system_organization": "Example",
            }
        }
    )
    readers_by_path = {"c.mmdb": country_reader, "a.mmdb": asn_reader}

    def fake_open(path: str) -> FakeGeoReader:
        calls.append(path)
        return readers_by_path[path]

    monkeypatch.setattr("worker.tools.geo_asn.open_reader", fake_open)

    tool = GeoAsnTool.from_settings(Settings(geoip_db_path="c.mmdb", geoip_asn_db_path="a.mmdb"))
    result = await tool.run({"ip": "203.0.113.10"}, _ctx())

    assert calls == ["c.mmdb", "a.mmdb"]
    assert result["country"] == "NL"
    assert result["asn"] == 64496


def test_tool_is_external() -> None:
    assert GeoAsnTool(country=None, asn=None).external is True


def test_geo_settings_and_secret_repr() -> None:
    settings = Settings()

    assert settings.maxmind_license_key.get_secret_value() == ""
    assert settings.geoip_db_path == ""
    assert settings.geoip_asn_db_path == ""

    secret_settings = Settings(maxmind_license_key=SecretStr("k-1"))
    assert "k-1" not in repr(secret_settings)


async def test_recorded_fixtures_replay_for_the_five_fixture_ips() -> None:
    """Review Finding M4: asserts each fixture's FULL result body (`ip`/`country`/`asn`/`org`),
    not just `country` — the brief's table pins these exact values (RFC 5398 documentation ASNs,
    chosen so no real ASN ever leaks into the repo) and a drifted `asn`/`org` previously shipped
    green.
    """
    recorder = ReplayToolRecorder(_FIXTURES_ROOT)
    tool = GeoAsnTool(country=None, asn=None)  # never called: replay never runs an external tool
    ctx = _ctx()

    expected_results = {
        "203.0.113.10": {
            "ip": "203.0.113.10",
            "country": "NL",
            "asn": 64496,
            "org": "Example Scanning BV",
        },
        "198.51.100.23": {
            "ip": "198.51.100.23",
            "country": "US",
            "asn": 64497,
            "org": "Example Cloud LLC",
        },
        "203.0.113.77": {
            "ip": "203.0.113.77",
            "country": "SG",
            "asn": 64498,
            "org": "Example Hosting Pte",
        },
        "192.0.2.55": {
            "ip": "192.0.2.55",
            "country": "DE",
            "asn": 64499,
            "org": "Example Hosting GmbH",
        },
        "198.51.100.140": {
            "ip": "198.51.100.140",
            "country": "BR",
            "asn": 64500,
            "org": "Example Telecom SA",
        },
    }

    for ip, expected in expected_results.items():
        arguments = {"ip": ip}

        result = await recorder.execute(tool, arguments, ctx)

        assert result == expected

        path = fixture_path(_FIXTURES_ROOT, tool.name, arguments)
        assert path.name == f"{fixture_key(arguments)}.json"
        data = json.loads(path.read_text())
        assert data["arguments"] == arguments
