"""Pins `sentinelbrief_shipper.config.ShipperConfig.from_env` (m6 task-02, honeypot shipper):
required vars, every default literal, numeric overrides, and secret hiding.

`sentinelbrief_shipper` is imported via `pythonpath = ["honeypot/shipper"]`
(`pyproject.toml`) — until the implementer adds the package under
`honeypot/shipper/sentinelbrief_shipper/`, every test below fails at collection with
`ModuleNotFoundError: No module named 'sentinelbrief_shipper'`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sentinelbrief_shipper.config import ShipperConfig

_REQUIRED_ENV = {
    "SHIPPER_INGEST_URL": "https://ingest.example.invalid/api/v1/alerts",
    "INGEST_HMAC_SECRET": "s3cr3t-required-value",
}


def test_from_env_requires_url_and_secret() -> None:
    """Interfaces `ShipperConfig.from_env`: each of the two required vars, missing alone, raises
    a `ValueError` naming that var and never echoing a value present in the other var — mutant:
    swallowing the missing-var case or leaking a configured value into the message.
    """
    canary_secret = "canary-secret-do-not-leak"
    with pytest.raises(ValueError) as missing_url:
        ShipperConfig.from_env({"INGEST_HMAC_SECRET": canary_secret})
    assert "SHIPPER_INGEST_URL" in str(missing_url.value)
    assert canary_secret not in str(missing_url.value)

    canary_url = "https://canary.invalid/api/v1/alerts"
    with pytest.raises(ValueError) as missing_secret:
        ShipperConfig.from_env({"SHIPPER_INGEST_URL": canary_url})
    assert "INGEST_HMAC_SECRET" in str(missing_secret.value)
    assert canary_url not in str(missing_secret.value)

    with pytest.raises(ValueError):
        ShipperConfig.from_env({})


def test_from_env_defaults_and_overrides() -> None:
    """Interfaces `ShipperConfig` field table: every default literal with only the two required
    vars set, then one override per numeric/path field — mutant: a wrong default literal or an
    override silently ignored.
    """
    defaults = ShipperConfig.from_env(_REQUIRED_ENV)

    assert defaults.ingest_url == _REQUIRED_ENV["SHIPPER_INGEST_URL"]
    assert defaults.hmac_secret == _REQUIRED_ENV["INGEST_HMAC_SECRET"]
    assert defaults.log_path == Path("/opt/sentinelbrief-honeypot/data/log/cowrie.json")
    assert defaults.state_dir == Path("/var/lib/sentinelbrief-shipper")
    assert defaults.idle_flush_s == 900.0
    assert defaults.max_events == 2000
    assert defaults.max_payload_bytes == 1_500_000
    assert defaults.post_timeout_s == 10.0
    assert defaults.backoff_base_s == 2.0
    assert defaults.backoff_max_s == 300.0
    assert defaults.spool_max_files == 10_000
    assert defaults.poll_interval_s == 1.0

    overrides = {
        **_REQUIRED_ENV,
        "SHIPPER_LOG_PATH": "/tmp/custom/cowrie.json",
        "SHIPPER_STATE_DIR": "/tmp/custom/state",
        "SHIPPER_IDLE_FLUSH_S": "120",
        "SHIPPER_MAX_EVENTS": "50",
        "SHIPPER_MAX_PAYLOAD_BYTES": "1000",
        "SHIPPER_POST_TIMEOUT_S": "5",
        "SHIPPER_BACKOFF_BASE_S": "1",
        "SHIPPER_BACKOFF_MAX_S": "30",
        "SHIPPER_SPOOL_MAX_FILES": "5",
        "SHIPPER_POLL_INTERVAL_S": "0.5",
    }
    overridden = ShipperConfig.from_env(overrides)

    assert overridden.log_path == Path("/tmp/custom/cowrie.json")
    assert overridden.state_dir == Path("/tmp/custom/state")
    assert overridden.idle_flush_s == 120.0
    assert overridden.max_events == 50
    assert overridden.max_payload_bytes == 1000
    assert overridden.post_timeout_s == 5.0
    assert overridden.backoff_base_s == 1.0
    assert overridden.backoff_max_s == 30.0
    assert overridden.spool_max_files == 5
    assert overridden.poll_interval_s == 0.5


def test_secret_absent_from_repr() -> None:
    """Interfaces `ShipperConfig.hmac_secret` sets `repr=False` — the secret must never appear in
    `repr(cfg)`; mutant: dropping `repr=False` from the dataclass field.
    """
    canary = "canary-secret-value-xyz"
    cfg = ShipperConfig.from_env({**_REQUIRED_ENV, "INGEST_HMAC_SECRET": canary})

    assert canary not in repr(cfg)
