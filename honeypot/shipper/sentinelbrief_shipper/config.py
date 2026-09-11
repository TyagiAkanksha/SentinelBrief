"""ShipperConfig (m6 task-02): environment-driven config for the honeypot log shipper.

Vendored independently of `core.config.Settings` — the shipper never imports the parent repo
(`tests/test_shipper_isolation.py`). `hmac_secret` sets `repr=False` so the one secret this
process ever holds (`INGEST_HMAC_SECRET`) can never leak through a `repr(cfg)` in a log line.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_LOG_PATH_DEFAULT = Path("/opt/sentinelbrief-honeypot/data/log/cowrie.json")
_STATE_DIR_DEFAULT = Path("/var/lib/sentinelbrief-shipper")


@dataclass(frozen=True)
class ShipperConfig:
    """Everything the shipper process needs, read once at startup from the environment."""

    ingest_url: str
    hmac_secret: str = field(repr=False)
    log_path: Path = _LOG_PATH_DEFAULT
    state_dir: Path = _STATE_DIR_DEFAULT
    idle_flush_s: float = 900.0
    max_events: int = 2000
    max_payload_bytes: int = 1_500_000
    post_timeout_s: float = 10.0
    backoff_base_s: float = 2.0
    backoff_max_s: float = 300.0
    spool_max_files: int = 10_000
    poll_interval_s: float = 1.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ShipperConfig:
        """Build a `ShipperConfig` from an environment mapping.

        Args:
            env: The environment mapping to read (`os.environ` in production).

        Returns:
            A populated `ShipperConfig`.

        Raises:
            ValueError: A required variable (`SHIPPER_INGEST_URL`, `INGEST_HMAC_SECRET`) is
                missing, or a numeric variable's value is not a positive number. The message
                names the variable only — it never echoes a configured value.
        """
        ingest_url = env.get("SHIPPER_INGEST_URL")
        if not ingest_url:
            raise ValueError("SHIPPER_INGEST_URL is required")
        hmac_secret = env.get("INGEST_HMAC_SECRET")
        if not hmac_secret:
            raise ValueError("INGEST_HMAC_SECRET is required")

        return cls(
            ingest_url=ingest_url,
            hmac_secret=hmac_secret,
            log_path=Path(env.get("SHIPPER_LOG_PATH", str(_LOG_PATH_DEFAULT))),
            state_dir=Path(env.get("SHIPPER_STATE_DIR", str(_STATE_DIR_DEFAULT))),
            idle_flush_s=_positive_float(env, "SHIPPER_IDLE_FLUSH_S", 900.0),
            max_events=_positive_int(env, "SHIPPER_MAX_EVENTS", 2000),
            max_payload_bytes=_positive_int(env, "SHIPPER_MAX_PAYLOAD_BYTES", 1_500_000),
            post_timeout_s=_positive_float(env, "SHIPPER_POST_TIMEOUT_S", 10.0),
            backoff_base_s=_positive_float(env, "SHIPPER_BACKOFF_BASE_S", 2.0),
            backoff_max_s=_positive_float(env, "SHIPPER_BACKOFF_MAX_S", 300.0),
            spool_max_files=_positive_int(env, "SHIPPER_SPOOL_MAX_FILES", 10_000),
            poll_interval_s=_positive_float(env, "SHIPPER_POLL_INTERVAL_S", 1.0),
        )


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    """Parse `env[name]` as a positive `float`, or return `default` when unset."""
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Parse `env[name]` as a positive `int`, or return `default` when unset."""
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value
