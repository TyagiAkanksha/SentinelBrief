"""Live smoke test for `worker.tools.ip_reputation.IpReputationTool` against the real AbuseIPDB
API (m4 task-04). Marked `@pytest.mark.live` (CONVENTIONS.md §9/§10): excluded from the default
`pytest -q` run and from CI via `addopts = "-m 'not live'"`. Run it explicitly with
`ABUSEIPDB_API_KEY` exported: `uv run pytest -m live tests/test_ip_reputation_live.py`. Never
asserts on or prints the API key. `1.1.1.1` (a public resolver) is the only place a non-RFC-5737
IP appears in this task's tests — shape only is asserted, never a real threat verdict.

The skip check reads `ABUSEIPDB_API_KEY` from `os.environ` directly (the same "skip by name"
pattern `tests/conftest.py` uses for `TEST_DATABASE_URL`) and `worker.tools.ip_reputation` is
imported lazily, inside the test, after that check — the module does not exist until this task's
GREEN step, so this file must stay collectible at RED (the pattern
`tests/test_cache.py::test_get_cache_returns_injected_instance` uses for `api/deps.py::get_cache`
before it exists).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

from core.cache import InMemoryTTLCache
from core.schemas.alert import CowrieEvent, SessionAlert
from worker.tools import ToolContext

_LIVE_IP = "1.1.1.1"


def _ctx() -> ToolContext:
    alert = SessionAlert(
        source="cowrie",
        session_id="s1",
        src_ip=_LIVE_IP,
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session="s1",
                src_ip=_LIVE_IP,
                sensor="sensor-1",
            )
        ],
    )
    return ToolContext(alert=alert, session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.live
async def test_live_check_returns_a_score_in_range() -> None:
    api_key = os.environ.get("ABUSEIPDB_API_KEY", "")
    if not api_key:
        pytest.skip("ABUSEIPDB_API_KEY not set")

    from core.config import Settings
    from worker.tools.ip_reputation import IpReputationTool

    settings = Settings()  # type: ignore[call-arg]

    async with httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s) as http:
        tool = IpReputationTool(
            api_key=api_key,
            http=http,
            cache=InMemoryTTLCache(),
            cache_ttl_s=settings.abuseipdb_cache_ttl_s,
            max_age_days=settings.abuseipdb_max_age_days,
        )

        result = await tool.run({"ip": _LIVE_IP}, _ctx())

    assert "unavailable" not in result, result
    assert 0 <= result["abuse_score"] <= 100
    assert result["reports"] >= 0
