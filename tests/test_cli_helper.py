"""Pins `core.cli` (`UsageError`, `Parser`, `fail`) — the one copy of the CLI presentation
helpers `worker/triage_one.py`, `evals/run.py` and `scripts/seed_dev.py` each carried a verbatim
copy of before this task (PRD §12 M0; CONVENTIONS.md §9: `scripts/` outside mypy but may import
`core.cli`; SUGGESTIONS.md t6-M6) — m5 task-05.

`Parser(prog).parse_args([...])` on a bad argv raises `UsageError` (never lets argparse
`SystemExit` the process) whose message ends with `"(see --help)"`; `fail(code, message)` prints
one flattened `error: <code>: <message>` line to stderr and returns `1`, collapsing embedded
newlines/repeated whitespace to single spaces exactly like the three CLIs' own former copies did.
The source-level pin at the end confirms this task actually deleted the three verbatim copies,
not merely added a fourth one alongside them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.cli import Parser, UsageError, fail
from core.errors import SentinelBriefError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLIS = (
    _REPO_ROOT / "worker" / "triage_one.py",
    _REPO_ROOT / "evals" / "run.py",
    _REPO_ROOT / "scripts" / "seed_dev.py",
)


def test_usage_error_is_a_sentinelbrief_error_with_the_usage_code() -> None:
    assert issubclass(UsageError, SentinelBriefError)
    assert UsageError.code == "usage"


def test_parser_raises_usage_error_and_fail_prints_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = Parser(prog="x")

    with pytest.raises(UsageError) as exc_info:
        parser.parse_args(["--nope"])
    assert str(exc_info.value).endswith("(see --help)")

    rc = fail("c", "a\nb  c")

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: c: a b c\n"


@pytest.mark.parametrize("cli_path", _CLIS, ids=lambda p: p.name)
def test_clis_no_longer_carry_their_own_copy(cli_path: Path) -> None:
    text = cli_path.read_text()
    assert "class _Parser" not in text
    assert "def _fail" not in text
    assert "class UsageError" not in text
