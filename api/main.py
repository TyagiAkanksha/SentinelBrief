"""Wiring entrypoint (CONVENTIONS.md §5): settings, engine, session factory, `create_app()`.

The only module that reads real secrets and fails fast on empty required ones. LLM/CHEAP_MODEL
guards and the triage seam are wired in task-04; this task wires the database only. Nothing
imports this module (import-linter contract 5) — it is a process entrypoint, not a library.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.factory import create_app
from core.config import Settings
from core.db import make_engine, make_session_factory
from core.errors import ConfigError


def _configure_logging() -> None:
    """Configure stdlib logging at INFO for the running process."""
    logging.basicConfig(level=logging.INFO)


def _require_nonempty(name: str, value: str) -> None:
    """Fail fast when a required secret is empty at boot.

    Args:
        name: The setting's name, for the error message.
        value: The plaintext secret value to check.

    Raises:
        ConfigError: When `value` is empty.
    """
    if not value:
        raise ConfigError(f"{name} must not be empty")


_configure_logging()

settings: Settings = Settings()
_require_nonempty("DATABASE_URL", settings.database_url.get_secret_value())
_require_nonempty("INGEST_HMAC_SECRET", settings.ingest_hmac_secret.get_secret_value())

engine: AsyncEngine = make_engine(settings.database_url.get_secret_value())
session_factory: async_sessionmaker[AsyncSession] = make_session_factory(engine)

app: FastAPI = create_app(session_factory=session_factory, settings=settings)
