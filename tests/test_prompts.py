"""Pins `worker.prompts`: the loader (`load_prompt`) and message builder (`build_messages`)
(m0 task-04).

PRD §10.6 (attacker-data delimiters, "data, never instructions"), §6.6 (rubric lives in the
prompt); CONVENTIONS.md §13 (every shipped prompt keeps the placeholder and markers; a shipped
version is immutable — the hash pin itself is added by the implementer in Step 6, not here).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.errors import ConfigError
from core.schemas.verdict import VERDICT_JSON_SCHEMA
from worker.prompts import (
    ALERT_DATA_BEGIN,
    ALERT_DATA_END,
    PROMPTS_DIR,
    SCHEMA_PLACEHOLDER,
    build_messages,
    load_prompt,
)
from worker.summarize import SessionSummary

# A minimal template exercising only what `build_messages` itself is responsible for: swapping
# the schema placeholder into the system message. Markers and rubric text are `triage-v1.md`'s
# job, pinned separately by `test_v1_has_placeholder_and_markers` and the parametrized test below.
_MINIMAL_TEMPLATE = "You are a triage assistant.\n\nOutput contract\n{{VERDICT_SCHEMA}}\n"


def _sample_summary() -> SessionSummary:
    return SessionSummary(
        source="cowrie",
        session_id="abc123",
        src_ip="203.0.113.5",
        sensor="hp-test-01",
        connect_time=datetime(2026, 1, 1, tzinfo=UTC),
        duration_ms=1500,
        client_version="SSH-2.0-OpenSSH_8.9",
        login_failed=3,
        login_success=1,
        usernames_sample=["root", "admin"],
        first_success_credential=("root", "toor"),
        command_count=2,
        download_count=1,
        upload_count=0,
    )


def test_v1_has_placeholder_and_markers() -> None:
    text = load_prompt("triage-v1")

    assert SCHEMA_PLACEHOLDER in text
    assert ALERT_DATA_BEGIN in text
    assert ALERT_DATA_END in text


def test_unknown_version_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        load_prompt("triage-v999-does-not-exist")


def test_prompt_missing_markers_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("worker.prompts.PROMPTS_DIR", tmp_path)
    (tmp_path / "triage-vtest.md").write_text(
        f"System prompt with the schema below but no attacker-data markers.\n{SCHEMA_PLACEHOLDER}\n"
    )

    with pytest.raises(ConfigError):
        load_prompt("triage-vtest")


def test_prompt_missing_placeholder_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("worker.prompts.PROMPTS_DIR", tmp_path)
    (tmp_path / "triage-vtest.md").write_text(
        "System prompt with markers but no schema placeholder.\n"
        f"{ALERT_DATA_BEGIN}\n...\n{ALERT_DATA_END}\n"
    )

    with pytest.raises(ConfigError):
        load_prompt("triage-vtest")


def test_at_least_one_shipped_prompt_exists() -> None:
    # Guards the parametrized test below against silently passing over an empty glob.
    assert len(list(PROMPTS_DIR.glob("triage-v*.md"))) >= 1


@pytest.mark.parametrize(
    "prompt_path",
    sorted(PROMPTS_DIR.glob("triage-v*.md")),
    ids=lambda p: p.name,
)
def test_every_shipped_prompt_has_placeholder_and_markers(prompt_path: Path) -> None:
    text = prompt_path.read_text()

    assert SCHEMA_PLACEHOLDER in text
    assert ALERT_DATA_BEGIN in text
    assert ALERT_DATA_END in text


def test_build_messages_wraps_summary_between_markers() -> None:
    summary = _sample_summary()

    messages = build_messages(_MINIMAL_TEMPLATE, summary=summary, schema=VERDICT_JSON_SCHEMA)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    assert content.startswith(ALERT_DATA_BEGIN)
    assert ALERT_DATA_END in content
    assert content.endswith("Return the verdict JSON object now.")
    begin_idx = content.index(ALERT_DATA_BEGIN)
    end_idx = content.index(ALERT_DATA_END)
    src_ip_idx = content.index(summary.src_ip)
    assert begin_idx < src_ip_idx < end_idx


def test_build_messages_substitutes_schema_json() -> None:
    summary = _sample_summary()

    messages = build_messages(_MINIMAL_TEMPLATE, summary=summary, schema=VERDICT_JSON_SCHEMA)

    system_content = messages[0]["content"]
    assert "severity" in system_content
    assert SCHEMA_PLACEHOLDER not in system_content
