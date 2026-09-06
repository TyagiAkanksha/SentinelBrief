"""`summarize_session`: the compact `SessionSummary` the first-pass triage prompt sees (PRD §1.2).

The first-pass prompt never sees the raw event list — only this summary (source IP, sensor,
duration, login counts, up to five distinct usernames, the first successful credential if any,
client banner, command/download/upload counts). The full command list is only reachable through
the `get_session_commands` tool (PRD §6.3, M4). Attacker-controlled strings that do appear here
(usernames, credential, client banner) are delimited as data by `worker.prompts.build_messages`,
never interpolated outside the markers.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from core.schemas.alert import SessionAlert

_LOGIN_EVENTS = {"cowrie.login.failed", "cowrie.login.success"}
_COMMAND_EVENTS = {"cowrie.command.input", "cowrie.command.failed"}
_USERNAME_SAMPLE_CAP = 5


class SessionSummary(BaseModel):
    """Compact, LLM-facing summary of one Cowrie session (PRD §1.2)."""

    source: str
    session_id: str
    src_ip: str
    sensor: str
    connect_time: datetime
    duration_ms: int | None
    client_version: str | None
    login_failed: int
    login_success: int
    usernames_sample: list[str]
    first_success_credential: tuple[str, str] | None
    command_count: int
    download_count: int
    upload_count: int


def summarize_session(alert: SessionAlert) -> SessionSummary:
    """Reduce `alert`'s full event list to the compact `SessionSummary`.

    Args:
        alert: The session alert to summarize.

    Returns:
        The `SessionSummary` derived from `alert.events`, in event order.
    """
    events = alert.events

    login_failed = sum(1 for e in events if e.eventid == "cowrie.login.failed")
    login_success = sum(1 for e in events if e.eventid == "cowrie.login.success")
    command_count = sum(1 for e in events if e.eventid in _COMMAND_EVENTS)
    download_count = sum(1 for e in events if e.eventid == "cowrie.session.file_download")
    upload_count = sum(1 for e in events if e.eventid == "cowrie.session.file_upload")

    client_version = next(
        (
            e.version
            for e in events
            if e.eventid == "cowrie.client.version" and e.version is not None
        ),
        None,
    )

    first_success_credential: tuple[str, str] | None = None
    for e in events:
        if e.eventid != "cowrie.login.success":
            continue
        if e.username is not None and e.password is not None:
            first_success_credential = (e.username, e.password)
            break

    usernames_sample: list[str] = []
    for e in events:
        if (
            e.eventid in _LOGIN_EVENTS
            and e.username is not None
            and e.username not in usernames_sample
        ):
            usernames_sample.append(e.username)
            if len(usernames_sample) == _USERNAME_SAMPLE_CAP:
                break

    return SessionSummary(
        source=alert.source,
        session_id=alert.session_id,
        src_ip=alert.src_ip,
        sensor=alert.sensor,
        connect_time=alert.connect_time,
        duration_ms=alert.duration_ms,
        client_version=client_version,
        login_failed=login_failed,
        login_success=login_success,
        usernames_sample=usernames_sample,
        first_success_credential=first_success_credential,
        command_count=command_count,
        download_count=download_count,
        upload_count=upload_count,
    )
