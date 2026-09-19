"""Regression tests (implementer-authored, unpinned) for the task-01 re-review's Important finding
N1: a malformed or pre-R10 candidates file must fail cleanly through `main`'s existing `io_error`
path — one stderr line, exit 1, never a traceback and never the row's own content — instead of a
raw `KeyError`/`TypeError` escaping `evals.label_tool._load_candidates` (m7 task-01 fix-3).

Covers exactly the three shapes the fix-3 brief names: a row missing `sampled.seed`, a row missing
`sampled.stratum_id`, and a row that isn't a JSON object at all — the CLI's never-a-traceback
contract (`core/cli.py`) must hold for all three, matching how a truncated or partially-written
file copied back from the box over SSM would actually look.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.label_tool import main


class FakeConsole:
    """A minimal `Console` double; every test here fails before any prompt is ever shown."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> None:
        self.written.append(text)

    def read(self, prompt: str) -> str:  # pragma: no cover - never reached in this module
        raise AssertionError("no test in this module should prompt the human")


_MINIMAL_ALERT = {
    "source": "cowrie",
    "session_id": "n1-malformed",
    "src_ip": "203.0.113.9",
    "sensor": "hp-n1",
    "events": [
        {
            "eventid": "cowrie.session.connect",
            "timestamp": "2026-09-06T00:00:00Z",
            "session": "n1-malformed",
            "src_ip": "203.0.113.9",
            "sensor": "hp-n1",
        }
    ],
}


def _run_label(tmp_path: Path, row_line: str) -> tuple[int, str, str]:
    candidates_path = tmp_path / "candidates.jsonl"
    candidates_path.write_text(row_line + "\n")
    out_path = tmp_path / "out.jsonl"
    rc = main(
        ["label", "--candidates", str(candidates_path), "--out", str(out_path)],
        console=FakeConsole(),
    )
    return rc, str(out_path), out_path.read_text() if out_path.exists() else ""


def test_main_label_missing_seed_exits_1_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = {
        "case_id": "abc123",
        "alert": _MINIMAL_ALERT,
        "sampled": {
            "alert_id": "a1",
            "received_at": "2026-09-06T00:00:00Z",
            "stratum_id": "deadbeef",
            # "seed" deliberately missing
        },
    }
    rc, _, out_text = _run_label(tmp_path, json.dumps(row))

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: io_error:")
    assert "Traceback" not in captured.err
    assert out_text == ""


def test_main_label_missing_stratum_id_exits_1_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = {
        "case_id": "abc123",
        "alert": _MINIMAL_ALERT,
        "sampled": {
            "alert_id": "a1",
            "received_at": "2026-09-06T00:00:00Z",
            "seed": 1,
            # "stratum_id" deliberately missing (e.g. a pre-R10 candidates file)
        },
    }
    rc, _, out_text = _run_label(tmp_path, json.dumps(row))

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: io_error:")
    assert "Traceback" not in captured.err
    assert out_text == ""


def test_main_label_non_object_row_exits_1_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # a line that parses as valid JSON but is not an object at all — e.g. a truncated write that
    # left a bare JSON array or string on its own line.
    rc, _, out_text = _run_label(tmp_path, json.dumps(["not", "an", "object"]))

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: io_error:")
    assert "Traceback" not in captured.err
    assert out_text == ""
