"""Pins `core.cli.Parser`'s unrecognized-argument hygiene fix (m6 task-06 fix-1 I2).

Before this fix, `Parser.error()` folded argparse's own "unrecognized arguments: <tokens>"
message into `UsageError` verbatim, and every caller (`scripts/seed_dev.py`, `evals/run.py`,
`worker/triage_one.py`, `scripts/check_real_sessions.py`) forwarded `str(e)` straight to
`core.cli.fail`, printing the leftover argv to stderr — which may be a DSN with a password
(`CONVENTIONS.md §7`: `DATABASE_URL` is a secret). The fix intercepts that ONE message shape
inside `Parser.error()` itself and replaces it with a fixed, redacted string before wrapping it
in `UsageError`, exactly like every other usage error — the existing "one stderr line, clean exit
1, `error: usage: ...`" contract (pinned by `tests/test_cli_helper.py`,
`tests/test_seed_dev.py::test_usage_error_exit_1`,
`tests/test_triage_one.py::test_usage_error_unknown_flag_exit_1`/
`test_usage_error_missing_path_exit_1`) is untouched: `worker/triage_one.py` explicitly reserves
exit code `2` for `VerdictValidationError`, so this fix deliberately does NOT switch to
argparse's own `SystemExit(2)` — only the message content changes, and only for this one shape.

m6 task-06 fix-2 N1: the "unrecognized arguments" substitution alone left a sibling argparse
shape open — `argument --limit: invalid int value: '<token>'` (from any `type=`-converting flag,
e.g. `--limit` on this script or `--concurrency` on `evals/run.py`) also echoes its raw token
verbatim, which could likewise be a DSN with a password. `Parser.error()` now also applies an
unconditional URL-like redaction to every message, closing this sibling shape on all four CLIs at
once; `test_limit_type_error_never_echoes_a_dsn` and
`test_evals_run_concurrency_type_error_never_echoes_a_dsn` below pin it on the two CLIs that
actually have a `type=`-converting non-`Path` flag.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from core.cli import Parser, UsageError, fail
from evals.run import main as evals_run_main

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_REAL_SESSIONS = REPO_ROOT / "scripts" / "check_real_sessions.py"

_CANARY_PASSWORD = "SUPERSECRETPW"
_CANARY_URL = f"postgresql://u:{_CANARY_PASSWORD}@h/db"


def _load_check_real_sessions() -> ModuleType:
    """Load `scripts/check_real_sessions.py` as a standalone module (no package `__init__.py`
    exists) — mirrors `tests/test_check_real_sessions.py`'s own loader; test files never import
    from each other, so this is a deliberate, small duplication.
    """
    spec = importlib.util.spec_from_file_location("check_real_sessions", CHECK_REAL_SESSIONS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unrecognized_argument_never_echoes_the_value() -> None:
    """A misspelled flag (`--dburl` instead of `--database-url`) carrying a DSN with a password
    must never put that password in the raised `UsageError`'s message (mutation self-check (c):
    reverting to argparse's own default `error()` makes this test fail, since the default
    message embeds `_CANARY_URL` verbatim).
    """
    parser = Parser(prog="check_real_sessions.py")
    parser.add_argument("--database-url", default=None)

    with pytest.raises(UsageError) as exc_info:
        parser.parse_args(["--dburl", _CANARY_URL])

    message = str(exc_info.value)
    assert _CANARY_PASSWORD not in message
    assert _CANARY_URL not in message
    assert message.endswith("(see --help)")
    assert "values withheld" in message


def test_fail_prints_the_redacted_message_not_the_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`core.cli.fail`, given the redacted `UsageError`, prints one stderr line — never the
    password — exactly like every other usage error on this `Parser`.
    """
    parser = Parser(prog="check_real_sessions.py")

    with pytest.raises(UsageError) as exc_info:
        parser.parse_args(["--dburl", _CANARY_URL])

    rc = fail(exc_info.value.code, str(exc_info.value))

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _CANARY_PASSWORD not in captured.err
    assert _CANARY_URL not in captured.err
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_limit_type_error_never_echoes_a_dsn(capsys: pytest.CaptureFixture[str]) -> None:
    """N1: `--limit <DSN>` fails `int` conversion, and argparse's own "invalid int value: '...'"
    message must never carry the password (mutation self-check (b): removing the unconditional
    `re.sub` redaction makes this test fail, since the raw DSN reaches stderr).
    """
    module = _load_check_real_sessions()

    rc = module.main(["--limit", _CANARY_URL])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _CANARY_PASSWORD not in captured.err
    assert _CANARY_URL not in captured.err
    assert "<redacted>" in captured.err
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_evals_run_concurrency_type_error_never_echoes_a_dsn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N1: `evals/run.py --concurrency <DSN>` fails its `_positive_int` conversion, and the
    resulting argparse message must never carry the password either — every CLI built on
    `core.cli.Parser` inherits the same fix.
    """
    rc = evals_run_main(["--concurrency", _CANARY_URL])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _CANARY_PASSWORD not in captured.err
    assert _CANARY_URL not in captured.err
    assert "<redacted>" in captured.err
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")


def test_check_real_sessions_main_inherits_the_fix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`scripts/check_real_sessions.py`'s own `main()` inherits the fix through `core.cli.Parser`
    — a misspelled flag carrying a DSN with a password never reaches stdout or stderr through
    this CLI either (Interfaces, brief line 83: "never prints a URL"), and the exit code stays
    the CLI's own `1`, never argparse's `2`.
    """
    module = _load_check_real_sessions()

    rc = module.main(["--dburl", _CANARY_URL])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _CANARY_PASSWORD not in captured.err
    assert _CANARY_URL not in captured.err
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: usage:")
