"""Pins ruling N1 (m7 task-02 re-review 1, fix round 2): `evals.run._tool_trace_sha256`'s own
DERIVATION, not merely its pass-through into `_case_payload` — the reviewer's mutation checks
showed a constant digest and a digest that drops `result` from the hash both survive the whole
suite (`tests/test_replay_strict_scope.py`'s wiring test and `tests/test_eval_determinism.py`'s
two-run comparison only ever observe two ALREADY-computed digests, never the function that
computes them from a real tool-call trace). This file drives `_tool_trace_sha256` directly: a
differing `result` changes the digest, a differing call ORDER changes it, the same trace repeated
is stable, and the empty trace is a documented constant (`sha256("[]")`) — killing both survivors.

Also pins the M2/N4 `not_recorded` guard in `evals.record.record` (`evals/record.py:206-211`): a
registry whose recorder never actually writes a fixture (e.g. a bare `LiveToolRecorder()` with no
`record_dir`) is a loud failure, never a silently-inflated `recorded` count over an empty
directory. Placed here rather than in the pinned `tests/test_record_poison.py` (the fix-2 brief
permits either) to keep that file's diff scoped to its one approved N2/R30 edit.

`evals.run._tool_trace_sha256` already computes a real digest today, so most tests in this file
are expected to be GREEN already — they exist to make regressing it (dropping a field,
short-circuiting, hardcoding a constant) fail loudly, per CLAUDE.md's "no test means the task is
not complete." The `not_recorded` guard test is likewise GREEN today (the guard already exists,
per the fix-1 M2 fix) — it was simply unpinned before this file.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.record import main as record_main
from evals.record import record
from evals.run import _tool_trace_sha256
from worker.outcome import ToolCallRecord
from worker.tools import ToolContext, ToolRegistry, fixture_key, fixture_path
from worker.tools.recorder import LiveToolRecorder

FIXTURES_DIR = Path("fixtures/alerts")

# The stable, well-known constant the ruling names: sha256 of the canonical JSON of an empty
# trace ("[]"), computed independently here (never imported from `evals.run`) so a change to the
# constant's *value* — not just its presence — is caught.
_EMPTY_TRACE_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def _call(
    seq: int, *, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
) -> ToolCallRecord:
    return ToolCallRecord(
        seq=seq, tool_name=tool_name, arguments=arguments, result=result, latency_ms=5
    )


# --- N1: _tool_trace_sha256's own derivation ----------------------------------------------------


def test_empty_trace_digest_is_the_documented_constant() -> None:
    assert _tool_trace_sha256(()) == _EMPTY_TRACE_SHA256
    # sanity: the constant really is sha256("[]"), not an arbitrary 64-hex string.
    assert _EMPTY_TRACE_SHA256 == hashlib.sha256(b"[]").hexdigest()


def test_differing_result_changes_the_digest() -> None:
    call_a = _call(
        0, tool_name="get_ip_geo_asn", arguments={"ip": "203.0.113.10"}, result={"country": "DE"}
    )
    call_b = _call(
        0, tool_name="get_ip_geo_asn", arguments={"ip": "203.0.113.10"}, result={"country": "SG"}
    )

    assert _tool_trace_sha256((call_a,)) != _tool_trace_sha256((call_b,))


def test_differing_call_order_changes_the_digest() -> None:
    call_a = _call(
        0, tool_name="get_ip_geo_asn", arguments={"ip": "203.0.113.10"}, result={"country": "DE"}
    )
    call_b = _call(
        1,
        tool_name="lookup_ip_reputation",
        arguments={"ip": "203.0.113.10"},
        result={"abuse_score": 0},
    )

    assert _tool_trace_sha256((call_a, call_b)) != _tool_trace_sha256((call_b, call_a))


def test_identical_trace_repeated_is_stable() -> None:
    def _trace() -> tuple[ToolCallRecord, ...]:
        return (
            _call(
                0,
                tool_name="get_ip_geo_asn",
                arguments={"ip": "203.0.113.10"},
                result={"country": "DE"},
            ),
            _call(
                1,
                tool_name="lookup_ip_reputation",
                arguments={"ip": "203.0.113.10"},
                result={"abuse_score": 0},
            ),
        )

    assert _tool_trace_sha256(_trace()) == _tool_trace_sha256(_trace())


# --- M2/N4: the not_recorded guard ----------------------------------------------------------------

_VALID_ALERT_PAYLOAD_NAME = "alert1.json"


def _alert(name: str, *, src_ip: str) -> SessionAlert:
    payload = json.loads((FIXTURES_DIR / name).read_text())
    payload["src_ip"] = src_ip
    return SessionAlert.model_validate(payload)


def _case(name: str, *, src_ip: str) -> GoldenCase:
    return GoldenCase(
        alert=_alert(name, src_ip=src_ip),
        label=GoldenLabel(severity=2, category="brute_force", escalate=False),
        labeler_note="synthetic case for the not_recorded guard test, not a scored claim.",
    )


class _CannedExternalTool:
    """A canned external-seam `Tool` stub answering a fixed successful result — mirrors
    `tests/test_record_poison.py::_CannedExternalTool` (test files never import from each other)."""

    description = "canned external tool stub for the not_recorded guard test"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": ["ip"],
        "additionalProperties": False,
    }
    external = True

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"ip": arguments["ip"], "tool": self.name}


def test_not_recorded_guard_when_the_registry_never_actually_writes_a_fixture(
    tmp_path: Path,
) -> None:
    """A registry wired with a bare `LiveToolRecorder()` (no `record_dir`) runs every tool
    successfully but never writes a fixture file anywhere — `record()` must report this as a loud
    `not_recorded` failure, never a silently-inflated `recorded` count over an empty directory
    (M2; the guard's own regression pin, previously missing per re-review N4).
    """
    fixtures_dir = tmp_path / "fixtures"
    ip = "203.0.113.99"
    cases = [_case(_VALID_ALERT_PAYLOAD_NAME, src_ip=ip)]
    tools: list[Any] = [
        _CannedExternalTool("get_ip_geo_asn"),
        _CannedExternalTool("lookup_ip_reputation"),
    ]
    # No `record_dir`: every call succeeds live but nothing is ever written to disk.
    misconfigured_registry = ToolRegistry(
        tools, recorder=LiveToolRecorder(), max_result_chars=10_000
    )
    key = fixture_key({"ip": ip})

    report = asyncio.run(
        record(
            cases,
            registry=misconfigured_registry,
            fixtures_dir=fixtures_dir,
            only=None,
            dry_run=False,
        )
    )

    assert report.recorded == 0
    assert sorted(report.failed) == sorted(
        [
            ("get_ip_geo_asn", key, "not_recorded"),
            ("lookup_ip_reputation", key, "not_recorded"),
        ]
    )
    assert not fixture_path(fixtures_dir, "get_ip_geo_asn", {"ip": ip}).exists()
    assert not fixture_path(fixtures_dir, "lookup_ip_reputation", {"ip": ip}).exists()

    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text(cases[0].model_dump_json() + "\n")
    rc = record_main(
        ["--golden", str(golden_path), "--fixtures", str(fixtures_dir)],
        registry=ToolRegistry(tools, recorder=LiveToolRecorder(), max_result_chars=10_000),
    )

    assert rc == 1
