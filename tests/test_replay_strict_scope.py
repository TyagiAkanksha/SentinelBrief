"""Pins ruling R26 (m7 task-02 fix-1, review I1): strict replay is scoped to the tools the
sampler can actually enumerate — the two `{"ip": ...}` tools (`get_ip_geo_asn`,
`lookup_ip_reputation`). `get_alert_history` is `external = True` too, but its `window_hours` is
the model's free choice, so `evals.record` can never pre-mint every fixture it might ask for; a
v2 run that made `get_alert_history` strict would fail non-deterministically depending on which
window the model happened to pick, exactly the opposite of the task's determinism goal. Strict
replay therefore raises `FixtureMissingError` only for a tool named in `strict_tools`
(`ReplayToolRecorder(fixtures_dir, *, strict=True, strict_tools=...)`); every other external tool
keeps the lenient M4-era `unavailable("fixture_missing")` degrade even under `strict=True`.

Also pins ruling R28: `evals.run._case_payload` carries a per-case `tool_trace_sha256` field, and
two `CaseResult`s built with differing trace digests round-trip as differing payload values —
`tests/test_eval_determinism.py` (pinned) is deliberately NOT extended for this (per the fix-1
brief); once `CaseResult` gains the field, that existing, untouched test's whole-payload
`payload1 == payload2` comparison automatically starts covering it too (the field flows through
`dataclasses.asdict` like every other `CaseResult` field), which is what kills the reviewer's
M-3c mutant (a replayed tool RESULT that silently differs between two runs) — this file only pins
the field's presence and its pass-through behavior in isolation.

`ReplayToolRecorder` does not accept `strict_tools` yet and `CaseResult`/`_case_payload` do not
carry `tool_trace_sha256` yet, so every test in this module is RED: a `TypeError` on the unknown
`strict_tools` keyword, or on the unknown `tool_trace_sha256` keyword to `CaseResult`. The
`get_alert_history`-under-`evals.run` test is RED because today `ReplayToolRecorder` applies the
strict raise to every external tool uniformly, so a missing `get_alert_history` fixture aborts
the case instead of completing it with a verdict.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pytest

from core.errors import FixtureMissingError
from core.schemas.alert import CowrieEvent, SessionAlert
from core.schemas.verdict import Verdict
from evals.golden import GoldenCase, GoldenLabel
from evals.run import _case_payload
from evals.run import main as run_main
from evals.scoring import CaseResult
from tests.fakes import FakeLLMClient, ScriptedToolCall
from worker.tools import ToolContext, unavailable
from worker.tools.recorder import ReplayToolRecorder

FIXTURES_DIR = Path("fixtures/alerts")

_VALID_VERDICT: dict[str, object] = {
    "severity": 2,
    "category": "brute_force",
    "confidence": 0.8,
    "reasoning": "ten failed logins with default credentials, no successful login observed.",
    "recommended_action": "monitor for continued brute-force activity.",
    "escalate": False,
}
VALID_VERDICT_JSON = json.dumps(_VALID_VERDICT)

_SETTINGS_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_JSON_MODE",
    "CHEAP_MODEL",
    "STRONG_MODEL",
    "MODEL_PRICES_JSON",
    "TRIAGE_PROMPT_VERSION",
    "ENVIRONMENT",
)


@pytest.fixture(autouse=True)
def _fake_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake, zero-priced model and no real API key for every test in this module."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHEAP_MODEL", "fake-model")
    monkeypatch.setenv(
        "MODEL_PRICES_JSON",
        '{"fake-model": {"input_per_mtok": "0", "output_per_mtok": "0"}}',
    )


def _golden_case(
    name: str, *, src_ip: str, labeled_by: Literal["human"] | None = None
) -> GoldenCase:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    payload["src_ip"] = src_ip
    alert = SessionAlert.model_validate(payload)
    return GoldenCase(
        alert=alert,
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for the strict-scope/history plumbing test, not scored.",
        labeled_by=labeled_by,
    )


def _write_golden(path: Path, cases: list[GoldenCase]) -> Path:
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
    return path


def _make_ctx() -> ToolContext:
    alert = SessionAlert(
        source="cowrie",
        session_id="s1",
        src_ip="203.0.113.10",
        sensor="sensor-1",
        events=[
            CowrieEvent(
                eventid="cowrie.session.connect",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                session="s1",
                src_ip="203.0.113.10",
                sensor="sensor-1",
            )
        ],
    )
    return ToolContext(alert=alert, session=None, now=datetime(2026, 1, 1, tzinfo=UTC))


class ExternalStub:
    """An external-seam `Tool` stub of a caller-chosen `name` — the external seam, not a mock of
    our own code (CONVENTIONS.md §10)."""

    description = "A stub external tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    external = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.run_count = 0

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        self.run_count += 1
        return {"live": True}


# --- R26: strict replay scoped to the two ip tools -----------------------------------------------


async def test_strict_scope_defaults_to_the_two_ip_tools(tmp_path: Path) -> None:
    ip_tool = ExternalStub("get_ip_geo_asn")
    history_tool = ExternalStub("get_alert_history")
    ctx = _make_ctx()
    recorder = ReplayToolRecorder(tmp_path, strict=True)  # default strict_tools

    with pytest.raises(FixtureMissingError):
        await recorder.execute(ip_tool, {"ip": "203.0.113.10"}, ctx)

    history_result = await recorder.execute(
        history_tool, {"ip": "203.0.113.10", "window_hours": 24}, ctx
    )

    assert history_result == unavailable("fixture_missing")
    assert history_tool.run_count == 0  # still never calls .run — only the raise is skipped


async def test_strict_tools_parameter_scopes_explicitly(tmp_path: Path) -> None:
    ip_tool = ExternalStub("get_ip_geo_asn")
    history_tool = ExternalStub("get_alert_history")
    ctx = _make_ctx()
    recorder = ReplayToolRecorder(tmp_path, strict=True, strict_tools=frozenset({"get_ip_geo_asn"}))

    with pytest.raises(FixtureMissingError):
        await recorder.execute(ip_tool, {"ip": "203.0.113.11"}, ctx)

    result = await recorder.execute(history_tool, {"ip": "203.0.113.11", "window_hours": 24}, ctx)

    assert result == unavailable("fixture_missing")


def test_get_alert_history_lenient_replay_still_completes_the_case(
    tmp_path: Path,
) -> None:
    fixtures_dir = tmp_path / "tool_fixtures"
    fixtures_dir.mkdir()  # no get_alert_history fixture for any window_hours, ever
    ip = "203.0.113.170"
    case = _golden_case("alert1.json", src_ip=ip, labeled_by="human")
    golden_path = _write_golden(tmp_path / "v2-history.jsonl", [case])
    output_dir = tmp_path / "results"
    output_dir.mkdir()

    fake = FakeLLMClient(
        [
            [ScriptedToolCall("get_alert_history", {"ip": ip, "window_hours": 999})],
            VALID_VERDICT_JSON,
        ]
    )

    rc = run_main(
        [
            "--golden",
            str(golden_path),
            "--prompt",
            "triage-v1",
            "--concurrency",
            "1",
            "--output-dir",
            str(output_dir),
            "--tool-fixtures",
            str(fixtures_dir),
            "--replay-strict",
        ],
        llm=fake,
    )

    written = list(output_dir.glob("*-triage-v1.json"))
    assert rc == 0
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["cases"][0]["error"] is None
    assert payload["cases"][0]["verdict"] is not None


# --- R28: _case_payload carries tool_trace_sha256 -------------------------------------------------


def _verdict() -> Verdict:
    return Verdict(
        severity=2,
        category="brute_force",
        confidence=0.8,
        reasoning="synthetic reasoning for the tool_trace_sha256 wiring test.",
        recommended_action="synthetic recommended action, distinct from the reasoning text.",
        escalate=False,
    )


def test_case_payload_carries_tool_trace_sha256_and_distinguishes_differing_traces() -> None:
    label = GoldenLabel(severity=2, category="brute_force", escalate=False)
    digest_a = hashlib.sha256(b"trace-a").hexdigest()
    digest_b = hashlib.sha256(b"trace-b").hexdigest()

    result_a = CaseResult(
        case_id="case-a",
        label=label,
        verdict=_verdict(),
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.000100"),
        latency_ms=5,
        error=None,
        tool_calls=1,
        escalated=False,
        tool_trace_sha256=digest_a,
    )
    result_b = replace(result_a, case_id="case-b", tool_trace_sha256=digest_b)

    payload_a = _case_payload(result_a)
    payload_b = _case_payload(result_b)

    assert payload_a["tool_trace_sha256"] == digest_a
    assert payload_b["tool_trace_sha256"] == digest_b
    assert payload_a["tool_trace_sha256"] != payload_b["tool_trace_sha256"]
