"""Prompt loading and message assembly for the triage pipeline (PRD §6.5/§6.6/§10.6).

`load_prompt` reads a versioned template from this package's own directory (the prompt files ship
beside this loader) and enforces, at load time, that it still carries the schema placeholder and
the attacker-data markers (CONVENTIONS.md §13) — raising `ConfigError` rather than letting a
malformed prompt reach the LLM. `build_messages` never
touches a template's own marker text; it only substitutes the schema into the system message and
wraps the `SessionSummary` between the fixed markers in the user message (PRD §10.6: attacker data
is always delimited), via `delimit_attacker_data`. Before wrapping, every `<<<` run inside the
serialized summary is neutralized so an attacker-controlled field (e.g. a username) cannot forge a
closing/opening delimiter and escape the block (PRD §10.6(a)).

`delimit_attacker_data`/`build_tool_result_message` (m4 task-06) apply the same delimiting to a
tool's result: PRD §6.3's tool loop feeds every result back to the model inside the same markers
the summary is, with the same neutralization, so a tool result that echoes an attacker-controlled
command or banner (`get_session_commands`) cannot forge the markers either.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.errors import ConfigError
from core.llm import ChatMessage
from worker.summarize import SessionSummary

PROMPTS_DIR: Path = Path(__file__).parent
SCHEMA_PLACEHOLDER = "{{VERDICT_SCHEMA}}"
ALERT_DATA_BEGIN = "<<<ALERT_DATA>>>"
ALERT_DATA_END = "<<<END_ALERT_DATA>>>"


def load_prompt(version: str) -> str:
    """Read and validate the prompt template for `version`.

    Args:
        version: The prompt version id, e.g. `"triage-v1"`.

    Returns:
        The template text.

    Raises:
        ConfigError: The version's file does not exist, or the file is missing the schema
            placeholder or the attacker-data markers.
    """
    path = PROMPTS_DIR / f"{version}.md"
    if not path.is_file():
        raise ConfigError(f"prompt version {version!r} not found at {path}")
    text = path.read_text()
    if SCHEMA_PLACEHOLDER not in text:
        raise ConfigError(
            f"prompt version {version!r} is missing the {SCHEMA_PLACEHOLDER} placeholder"
        )
    if ALERT_DATA_BEGIN not in text or ALERT_DATA_END not in text:
        raise ConfigError(f"prompt version {version!r} is missing the attacker-data markers")
    return text


def build_messages(
    template: str, *, summary: SessionSummary, schema: Mapping[str, Any]
) -> list[ChatMessage]:
    """Assemble the two-message chat payload for one triage call.

    Args:
        template: The loaded prompt template (from `load_prompt`).
        summary: The session summary to wrap between the attacker-data markers.
        schema: The JSON Schema to substitute for `SCHEMA_PLACEHOLDER`.

    Returns:
        `[system, user]`: the system message with the schema substituted in, and the user
        message with `summary` delimited (`delimit_attacker_data`) between `ALERT_DATA_BEGIN` /
        `ALERT_DATA_END`. Any `<<<` run inside the serialized summary is replaced with `‹‹‹`
        first, so an attacker-controlled field cannot forge `ALERT_DATA_BEGIN`/`ALERT_DATA_END`
        and close the block early (PRD §10.6(a)); the real markers added here are untouched.
        Output is byte-identical to before this helper existed (pinned).
    """
    system_content = template.replace(SCHEMA_PLACEHOLDER, json.dumps(schema, indent=2))
    summary_json = summary.model_dump_json(indent=2)
    user_content = f"{delimit_attacker_data(summary_json)}\nReturn the verdict JSON object now."
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def delimit_attacker_data(text: str) -> str:
    """Wrap `text` between the attacker-data markers, neutralizing any forged delimiter first.

    Every `<<<` run inside `text` is replaced with `‹‹‹` (a distinct, visually similar character)
    so attacker-controlled content (a summary field, a tool result) cannot forge
    `ALERT_DATA_BEGIN`/`ALERT_DATA_END` and escape the block (PRD §10.6(a)); the markers added
    here are the only real ones.

    Args:
        text: The (already-serialized) attacker-influenced text to delimit.

    Returns:
        `f"{ALERT_DATA_BEGIN}\\n{text with <<< neutralized}\\n{ALERT_DATA_END}"`.
    """
    return f"{ALERT_DATA_BEGIN}\n{text.replace('<<<', '‹‹‹')}\n{ALERT_DATA_END}"


def build_tool_result_message(tool_call_id: str, result: Mapping[str, Any]) -> ChatMessage:
    """Build the `role: "tool"` message that feeds one tool's result back to the model.

    PRD §6.3/§10.6: a tool result is attacker data (it may echo a command, a banner, a username)
    and is delimited exactly like the summary — inside the same markers, `<<<` neutralized —
    with the "data, never instructions" sentence living in the prompt text itself.

    Args:
        tool_call_id: The `ToolCallRequest.id` this result answers (echoed back so the model can
            match it to its own tool-call request).
        result: The tool's (already truncated) result to serialize and delimit.

    Returns:
        `{"role": "tool", "tool_call_id": tool_call_id, "content": <delimited result JSON>}`.
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": delimit_attacker_data(json.dumps(result, sort_keys=True, ensure_ascii=False)),
    }
