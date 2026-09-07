"""`SessionAlert`: the session-level alert envelope (PRD §1.2, §6.1 step 2).

One alert is one Cowrie session: every event sharing a `session` id, in timestamp order. Connect
time, close time and `duration_ms` are derived from the first event and the `cowrie.session.closed`
event; the fingerprint (`sha256(source|session_id|connect_time_utc_iso)`) is the ingest
deduplication key (PRD §6.1 step 2) — duplicates never re-trigger triage.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CowrieEvent(BaseModel):
    """One raw Cowrie JSON event; unlisted fields survive via `extra="allow"` for forensics."""

    model_config = ConfigDict(extra="allow")

    eventid: str
    timestamp: datetime
    session: str
    src_ip: str
    sensor: str
    message: str = ""
    username: str | None = None
    password: str | None = None
    input: str | None = None
    url: str | None = None
    outfile: str | None = None
    shasum: str | None = None
    duration_ms: int | None = None
    version: str | None = None


class SessionAlert(BaseModel):
    """The `raw` alert payload: one Cowrie session's events, ordered, plus the envelope fields."""

    model_config = ConfigDict(extra="allow")

    source: Literal["cowrie"]
    session_id: str
    src_ip: str
    sensor: str
    events: Annotated[list[CowrieEvent], Field(min_length=1)]

    @model_validator(mode="after")
    def _sort_and_require_connect_first(self) -> SessionAlert:
        """Sort events by timestamp and require the first (post-sort) event to be a connect.

        A stable sort on `timestamp` means events sharing an instant keep their input order.
        Requiring `cowrie.session.connect` first *after* sorting catches malformed sessions
        (e.g. an event that predates the connect) rather than merely the raw input order.
        """
        sorted_events = sorted(self.events, key=lambda e: e.timestamp)
        if sorted_events[0].eventid != "cowrie.session.connect":
            raise ValueError("events[0] must be cowrie.session.connect after sorting")
        self.events = sorted_events
        return self

    @property
    def connect_time(self) -> datetime:
        """The session's connect time: `events[0].timestamp`."""
        return self.events[0].timestamp

    @property
    def close_time(self) -> datetime | None:
        """The timestamp of the last `cowrie.session.closed` event, or None if never closed."""
        closed_events = [e for e in self.events if e.eventid == "cowrie.session.closed"]
        if not closed_events:
            return None
        return closed_events[-1].timestamp

    @property
    def duration_ms(self) -> int | None:
        """Session duration in milliseconds.

        Prefers the `cowrie.session.closed` event's own `duration_ms` field; falls back to a
        recomputed delta between connect and close time; None if the session never closed.
        """
        closed_events = [e for e in self.events if e.eventid == "cowrie.session.closed"]
        if closed_events and closed_events[-1].duration_ms is not None:
            return closed_events[-1].duration_ms
        close_time = self.close_time
        if close_time is None:
            return None
        delta = close_time - self.connect_time
        return int(delta / timedelta(milliseconds=1))

    def fingerprint(self) -> str:
        """The PRD §6.1 dedup key: sha256("source|session_id|connect_time_utc_iso")."""
        connect_utc_iso = self.connect_time.astimezone(UTC).isoformat()
        raw = "|".join([self.source, self.session_id, connect_utc_iso])
        return hashlib.sha256(raw.encode()).hexdigest()
