"""SessionAssembler (m6 task-02): groups Cowrie events by `session` and emits a signable payload
the moment a session closes, or after an idle timeout for a session Cowrie never closes (a Cowrie
restart mid-session).

PRD §10.6 rule 1: attacker-controlled strings (usernames, passwords, commands, banners, URLs) are
never logged — every log line below carries a session id and/or a counter, nothing else.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sentinelbrief_shipper import __version__

CLOSED_EVENT = "cowrie.session.closed"
CONNECT_EVENT = "cowrie.session.connect"

_REQUIRED_FIELDS = ("eventid", "session", "src_ip", "sensor", "timestamp")

logger = logging.getLogger(__name__)


@dataclass
class OpenSession:
    """One in-progress Cowrie session being accumulated by the assembler."""

    session_id: str
    src_ip: str
    sensor: str
    events: list[dict[str, Any]]
    last_seen: float
    has_connect: bool


@dataclass(frozen=True)
class AssemblerStats:
    """Counters only — never the raw offending text (PRD §10.6 rule 1)."""

    parse_errors: int
    dropped_no_connect: int
    truncated_sessions: int


class SessionAssembler:
    """Accumulates Cowrie events per session, emitting a payload on close or idle timeout."""

    def __init__(
        self,
        *,
        idle_flush_s: float,
        max_events: int,
        max_payload_bytes: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Set the per-session caps and idle timeout.

        Args:
            idle_flush_s: A session with no event for this long is shipped without a closed
                event (`flush_idle`).
            max_events: Per-session event cap (`build_payload` cap 1).
            max_payload_bytes: Serialized payload byte cap (`build_payload` cap 2).
            clock: Injected clock (tests only); defaults to `time.monotonic`.
        """
        self._idle_flush_s = idle_flush_s
        self._max_events = max_events
        self._max_payload_bytes = max_payload_bytes
        self._clock = clock
        self._sessions: dict[str, OpenSession] = {}
        self._parse_errors = 0
        self._dropped_no_connect = 0
        self._truncated_sessions = 0

    def feed(self, line: str) -> list[bytes]:
        """Parse one raw log line and fold it into its session.

        Args:
            line: One raw Cowrie JSON log line.

        Returns:
            `[payload]` once this line closes a session with a prior connect event; `[]`
            otherwise (including a malformed line, or a session closed with no prior connect —
            both counted in `stats`, never shipped).
        """
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        if not isinstance(event, dict) or not all(f in event for f in _REQUIRED_FIELDS):
            self._parse_errors += 1
            logger.warning("shipper: unparseable log line skipped (count=%d)", self._parse_errors)
            return []

        session_id = event["session"]
        session = self._sessions.get(session_id)
        if session is None:
            session = OpenSession(
                session_id=session_id,
                src_ip=event["src_ip"],
                sensor=event["sensor"],
                events=[],
                last_seen=self._clock(),
                has_connect=event["eventid"] == CONNECT_EVENT,
            )
            self._sessions[session_id] = session
        session.events.append(event)
        session.last_seen = self._clock()

        if event["eventid"] != CLOSED_EVENT:
            return []

        del self._sessions[session_id]
        if not session.has_connect:
            self._dropped_no_connect += 1
            logger.warning("shipper: session dropped, no connect event session_id=%s", session_id)
            return []
        return [self.build_payload(session)]

    def flush_idle(self) -> list[bytes]:
        """Ship (or drop) every open session idle for at least `idle_flush_s`.

        Returns:
            One payload per flushed session that has a connect event, in no particular order; a
            never-closed session with no connect event is dropped with the same WARNING `feed`
            uses. Every flushed session id is forgotten.
        """
        now = self._clock()
        stale_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_seen >= self._idle_flush_s
        ]
        payloads: list[bytes] = []
        for session_id in stale_ids:
            session = self._sessions.pop(session_id)
            if not session.has_connect:
                self._dropped_no_connect += 1
                logger.warning(
                    "shipper: session dropped, no connect event session_id=%s", session_id
                )
                continue
            payloads.append(self.build_payload(session))
        return payloads

    def build_payload(self, session: OpenSession) -> bytes:
        """Serialize `session` to deterministic, signable bytes, applying the two caps.

        Args:
            session: The session to serialize (events in arrival order — the api sorts by
                timestamp; Cowrie writes in order).

        Returns:
            `json.dumps(envelope, separators=(",", ":"), sort_keys=True,
            ensure_ascii=False).encode("utf-8")`. Cap 1 keeps `events[:max_events-1] +
            [events[-1]]` when over `max_events`. Cap 2 then halves the kept events (always
            keeping the first and last) until the serialized size is at or under
            `max_payload_bytes`, or only 2 events remain. `envelope["shipper"]` is present only
            when at least one event was dropped by either cap.
        """
        total = len(session.events)
        events = list(session.events)
        if len(events) > self._max_events:
            events = events[: self._max_events - 1] + [events[-1]]

        envelope: dict[str, Any] = {
            "source": "cowrie",
            "session_id": session.session_id,
            "src_ip": session.src_ip,
            "sensor": session.sensor,
            "events": events,
        }
        payload = self._dumps(envelope)

        while len(payload) > self._max_payload_bytes and len(events) > 2:
            events = events[: max(2, len(events) // 2) - 1] + [events[-1]]
            envelope["events"] = events
            payload = self._dumps(envelope)

        truncated = total - len(events)
        if truncated > 0:
            envelope["shipper"] = {"version": __version__, "truncated_events": truncated}
            payload = self._dumps(envelope)
            self._truncated_sessions += 1

        return payload

    @staticmethod
    def _dumps(envelope: dict[str, Any]) -> bytes:
        """The one deterministic serialization every payload and its signature go through."""
        text = json.dumps(envelope, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        return text.encode("utf-8")

    @property
    def stats(self) -> AssemblerStats:
        """Current parse-error/no-connect/truncation counters."""
        return AssemblerStats(
            parse_errors=self._parse_errors,
            dropped_no_connect=self._dropped_no_connect,
            truncated_sessions=self._truncated_sessions,
        )

    @property
    def open_count(self) -> int:
        """The number of sessions currently open (neither closed nor idle-flushed)."""
        return len(self._sessions)
