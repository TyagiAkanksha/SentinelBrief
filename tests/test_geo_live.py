"""Live smoke tests for `get_ip_geo_asn` over a real downloaded GeoLite2 database and
`scripts/fetch_geoip.py` against the real MaxMind endpoint (m4 task-03). `@pytest.mark.live` —
CONVENTIONS.md §10: excluded from the default `pytest` run (`addopts = "-m 'not live'"`) and from
CI; never run automatically.

Each test checks its own required environment variable (and, for the country lookup, the
downloaded file's existence) with a plain `pytest.skip(...)` call *before* importing anything from
`worker.tools.geo_asn` or loading `scripts/fetch_geoip.py` — so both skip cleanly even before
either module exists (this task's own RED state), rather than turning a legitimate "not
configured" skip into a collection error. The `Settings` lookups use `getattr(..., "")`/`None`
defaults rather than the eventual `settings.geoip_db_path` / `settings.maxmind_license_key`
attribute access directly, so the same skip fires even before `core/config.py` grows those fields
(also this task's own RED state) — after GREEN, `getattr` reads the real fields exactly the same
way direct attribute access would.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from core.config import Settings
from core.schemas.alert import CowrieEvent, SessionAlert

pytestmark = pytest.mark.live

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "fetch_geoip.py"


def _load_fetch_geoip() -> ModuleType:
    """Load `scripts/fetch_geoip.py` as a standalone module (no package `__init__.py` exists)."""
    spec = importlib.util.spec_from_file_location("fetch_geoip", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_live_country_lookup_with_the_downloaded_database() -> None:
    settings = Settings()
    geoip_db_path = getattr(settings, "geoip_db_path", "")
    if not geoip_db_path or not Path(geoip_db_path).is_file():
        pytest.skip("GEOIP_DB_PATH is not set or the file does not exist")

    from worker.tools.base import ToolContext
    from worker.tools.geo_asn import GeoAsnTool

    tool = GeoAsnTool.from_settings(settings)
    alert = SessionAlert(
        source="cowrie",
        session_id="s1",
        src_ip="1.1.1.1",
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session="s1",
                src_ip="1.1.1.1",
                sensor="sensor-1",
            )
        ],
    )
    ctx = ToolContext(alert=alert, session=None, now=datetime(2026, 1, 1, tzinfo=UTC))

    result = await tool.run({"ip": "1.1.1.1"}, ctx)

    assert result["country"] is not None
    assert re.fullmatch(r"[A-Z]{2}", result["country"])


def test_live_fetch_geoip_downloads_a_readable_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings()
    license_key = getattr(settings, "maxmind_license_key", None)
    key = license_key.get_secret_value() if license_key is not None else ""
    if not key:
        pytest.skip("MAXMIND_LICENSE_KEY is not set")

    from worker.tools.geo_asn import open_reader

    fetch_geoip = _load_fetch_geoip()

    exit_code = fetch_geoip.main(["--out-dir", str(tmp_path), "--edition", "GeoLite2-Country"])

    assert exit_code == 0
    reader = open_reader(str(tmp_path / "GeoLite2-Country.mmdb"))
    assert reader is not None

    captured = capsys.readouterr()
    assert key not in captured.out
    assert key not in captured.err
