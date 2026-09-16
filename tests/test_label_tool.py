"""Pins `evals/label_tool.py`: the author's labeling CLI, its resumability, its seeded 10 %
re-review, `stats`, and the "only this file may write `labeled_by: human`" guarantee (PRD §6.6,
§7.1, §13; m7 task-01).

`evals.label_tool` (and `evals.sample`, which every `Candidate` fixture below is built through)
does not exist yet, so every test in this module is RED at collection with `ModuleNotFoundError`,
not merely at first use.

Every test drives `prompt_label`/`label`/`rereview` through `FakeConsole` (this module's own
`Console` double, injected exactly like `tests/fakes.py::FakeLLMClient` is injected everywhere
else — CONVENTIONS.md §10: mock only the seam, never our own code) feeding scripted answers;
nothing here types a real label, and nothing here creates or reads `evals/golden/v2.jsonl`
(PRD §13).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.schemas.alert import SessionAlert
from evals.golden import GoldenCase, GoldenLabel
from evals.label_tool import label, prompt_label, rereview, stats
from evals.sample import Candidate, write_candidates
from tests.helpers import load_alert

REPO_ROOT = Path(__file__).resolve().parent.parent
_ATTACKER_MARKER = "CANARY-ATTACKER-COMMAND-XYZ"


class FakeConsole:
    """The `Console` double every test in this module injects: scripted `read()` answers, and
    every `write()` call recorded verbatim for assertions — never printed to a real terminal."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = iter(answers)
        self.written: list[str] = []

    def write(self, text: str) -> None:
        self.written.append(text)

    def read(self, prompt: str) -> str:
        self.written.append(prompt)
        return next(self._answers)


def _candidate(idx: int, *, stratum: str = "scanning") -> Candidate:
    alert = load_alert("alert1", session_id=f"label-tool-{idx}")
    return Candidate(
        case_id=alert.fingerprint(),
        alert_id=f"alert-id-{idx}",
        received_at=datetime(2026, 9, 6, tzinfo=UTC),
        stratum=stratum,
        alert=alert,
    )


def _candidate_with_attacker_text(idx: int) -> Candidate:
    """`fixtures/alerts/alert1.json` with one `cowrie.command.input` event carrying
    `_ATTACKER_MARKER`, for the "never logs case content" negative pin."""
    data = load_alert("alert1", session_id=f"attacker-{idx}").model_dump(mode="json")
    connect_ts = datetime.fromisoformat(data["events"][0]["timestamp"])
    data["events"].insert(
        1,
        {
            "eventid": "cowrie.command.input",
            "timestamp": connect_ts.isoformat(),
            "session": data["session_id"],
            "src_ip": data["src_ip"],
            "sensor": data["sensor"],
            "input": _ATTACKER_MARKER,
        },
    )
    alert = SessionAlert.model_validate(data)
    return Candidate(
        case_id=alert.fingerprint(),
        alert_id=f"alert-id-{idx}",
        received_at=datetime(2026, 9, 6, tzinfo=UTC),
        stratum="scanning",
        alert=alert,
    )


def _human_row(
    idx: int, *, severity: int, category: str, tags: list[str] | None = None
) -> GoldenCase:
    alert = load_alert("alert1", session_id=f"row-{idx}")
    return GoldenCase(
        alert=alert,
        label=GoldenLabel(severity=severity, category=category, escalate=severity >= 4),
        labeler_note=f"§6.6 sev {severity}: synthetic fixture row {idx} for label_tool tests.",
        tags=tags or [],
        labeled_by="human",
        labeled_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


def _legacy_row(idx: int) -> GoldenCase:
    """A v1-shaped row (no `labeled_by`) — `stats`'s `labeled_by` breakdown must distinguish it
    from the human-labeled rows above."""
    alert = load_alert("alert1", session_id=f"legacy-{idx}")
    return GoldenCase(
        alert=alert,
        label=GoldenLabel(severity=1, category="scanning", escalate=False),
        labeler_note="§6.6 sev 1: legacy-shaped row with no labeled_by, for the stats breakdown.",
        tags=[],
    )


# --- prompt_label: typed input only, nothing invented --------------------------------------------


def test_prompt_label_records_typed_input_only() -> None:
    candidate = _candidate(1, stratum="injection-candidate")
    console = FakeConsole(
        [
            "4",  # severity
            "4",  # category menu: STRATA_CATEGORIES[3] == "successful_intrusion"
            "not citing the rubric row",  # missing "6.6" -> must re-prompt
            "§6.6 sev 4: successful login, no further compromise observed.",
            "injection",  # tags
        ]
    )

    result = prompt_label(console, candidate)

    assert result is not None
    assert result.alert == candidate.alert
    assert result.label.severity == 4
    assert result.label.category == "successful_intrusion"
    assert result.label.escalate is True  # auto for severity >= 4, never asked
    assert result.labeler_note == "§6.6 sev 4: successful login, no further compromise observed."
    assert result.tags == ["injection"]
    assert result.labeled_by == "human"
    assert result.labeled_at is not None


# --- label(): resumable, skip, quit ---------------------------------------------------------------


def test_label_skips_cases_already_in_out_file(tmp_path: Path) -> None:
    candidate_a = _candidate(1)
    candidate_b = _candidate(2)
    candidates_path = tmp_path / "candidates.jsonl"
    write_candidates(candidates_path, [candidate_a, candidate_b], seed=20260914)
    out_path = tmp_path / "v2-out.jsonl"

    console_1 = FakeConsole(
        ["2", "2", "§6.6 sev 2: generic default-credential spray, no success.", "", "q"]
    )
    n1 = label(console_1, candidates_path, out_path)
    assert n1 == 1
    first_pass = out_path.read_text().splitlines()
    assert len(first_pass) == 1
    assert json.loads(first_pass[0])["alert"]["session_id"] == candidate_a.alert.session_id

    console_2 = FakeConsole(["3", "3", "§6.6 sev 3: host-derived username spray, no success.", ""])
    n2 = label(console_2, candidates_path, out_path)
    assert n2 == 1

    lines = out_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["alert"]["session_id"] == candidate_b.alert.session_id


def test_skip_and_quit(tmp_path: Path) -> None:
    candidates = [_candidate(1), _candidate(2), _candidate(3)]
    candidates_path = tmp_path / "candidates.jsonl"
    write_candidates(candidates_path, candidates, seed=20260914)
    out_path = tmp_path / "out.jsonl"

    console = FakeConsole(
        ["s", "2", "2", "§6.6 sev 2: generic automation, no success outcome.", "", "q"]
    )
    n = label(console, candidates_path, out_path)

    assert n == 1
    assert out_path.exists()
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["alert"]["session_id"] == candidates[1].alert.session_id


# --- rereview: seeded 10 % sample, disagreement rate ----------------------------------------------


def test_rereview_samples_fraction_and_reports_disagreement(tmp_path: Path) -> None:
    rows = [_human_row(i, severity=2, category="brute_force") for i in range(40)]
    golden_path = tmp_path / "v2-rereview-source.jsonl"
    golden_path.write_text("\n".join(r.model_dump_json() for r in rows) + "\n")
    out_path = tmp_path / "v2-rereview.jsonl"

    agree_answers = ["2", "2", "§6.6 sev 2: matches the original label, generic spray.", ""]
    disagree_answers = ["5", "5", "§6.6 sev 5: reviewer disagrees, malware delivery seen.", ""]
    console = FakeConsole([*agree_answers, *agree_answers, *agree_answers, *disagree_answers])

    rate = rereview(console, golden_path, out_path, fraction=0.10, seed=2026)

    assert rate == 0.25
    messages = "\n".join(console.written)
    assert "25.0 % (1/4)" in messages
    assert "> 10 % — fix the rubric and relabel (PRD §7.1)" in messages

    lines = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 4
    for row in lines:
        assert set(row.keys()) == {"case_id", "first", "second"}


# --- stats: the acceptance-walk table -------------------------------------------------------------


def test_stats_table_counts(tmp_path: Path) -> None:
    rows = [
        _human_row(1, severity=1, category="scanning"),
        _human_row(2, severity=1, category="scanning"),
        _human_row(3, severity=2, category="brute_force"),
        _human_row(4, severity=4, category="successful_intrusion", tags=["injection"]),
        _human_row(5, severity=4, category="successful_intrusion"),
        _legacy_row(6),
    ]
    golden_path = tmp_path / "v2-stats.jsonl"
    golden_path.write_text("\n".join(r.model_dump_json() for r in rows) + "\n")

    result = stats(golden_path)

    assert isinstance(result, str) and result
    assert re.search(r"scanning\D{0,10}3\b", result)
    assert re.search(r"brute_force\D{0,10}1\b", result)
    assert re.search(r"successful_intrusion\D{0,10}2\b", result)
    assert re.search(r"injection\D{0,10}1\b", result)
    assert re.search(r"human\D{0,10}5\b", result)


# --- ONLY label_tool.py::prompt_label may write labeled_by="human" --------------------------------


_LABELED_BY_HUMAN = re.compile(r'labeled_by\s*=\s*"human"')


def test_only_label_tool_writes_labeled_by_human() -> None:
    hits: list[Path] = []
    for root_name in ("evals", "scripts"):
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if _LABELED_BY_HUMAN.search(path.read_text()):
                hits.append(path.relative_to(REPO_ROOT))

    assert hits == [Path("evals/label_tool.py")], (
        f"labeled_by='human' must be written only in evals/label_tool.py; found in: {hits}"
    )

    sample_source = (REPO_ROOT / "evals" / "sample.py").read_text()
    assert "GoldenLabel" not in sample_source, "evals/sample.py must never import GoldenLabel"


# --- attacker text reaches the human's terminal, never a log --------------------------------------


def test_label_tool_never_logs_case_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    candidate = _candidate_with_attacker_text(1)
    candidates_path = tmp_path / "candidates.jsonl"
    write_candidates(candidates_path, [candidate], seed=20260914)
    out_path = tmp_path / "out.jsonl"
    console = FakeConsole(["2", "1", "§6.6 sev 2: generic automation, no success observed.", ""])

    with caplog.at_level(logging.DEBUG):
        n = label(console, candidates_path, out_path)

    assert n == 1
    assert _ATTACKER_MARKER in "\n".join(console.written), (
        "sanity: render_case must actually show the candidate's command to the human"
    )
    assert _ATTACKER_MARKER not in caplog.text
