"""Declarative base and the shared primary-key helper (CONVENTIONS.md §6)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for every SentinelBrief ORM model.

    Tables carry no explicit `schema=` so they create into whatever schema is first on the
    connection's search_path — the throwaway test schema, or `public` in production (see
    `core.db.make_engine`).
    """


def uuid_pk() -> Mapped[uuid.UUID]:
    """Shared primary-key column: a server-generated UUID via Postgres's `gen_random_uuid()`."""
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
