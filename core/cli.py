"""The one copy of the CLI presentation helpers shared by every SentinelBrief CLI
(CONVENTIONS.md §4/§9; SUGGESTIONS.md t6-M6) — m5 task-05.

`worker/triage_one.py`, `evals/run.py` and `scripts/seed_dev.py` each carried a byte-identical
copy of `UsageError`/`_Parser`/`_fail` before this task; all three now import from here instead.
`scripts/` sits outside mypy's `strict` file list (CONVENTIONS.md §9) but may still import
`core.cli` like any other module.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import NoReturn

from core.errors import SentinelBriefError

_URL_LIKE = re.compile(r"\S+://\S+")


class UsageError(SentinelBriefError):
    """CLI-only: raised by `Parser.error` instead of letting argparse exit the process directly.

    Not a domain error any other layer needs to catch — a malformed command line is a CLI
    presentation concern.
    """

    code = "usage"


class Parser(argparse.ArgumentParser):
    """An `ArgumentParser` that raises `UsageError` on a usage error instead of exiting.

    `--help` is unaffected: it exits via `self.exit(0, ...)` in argparse's own help action, which
    never calls `error()`.
    """

    def error(self, message: str) -> NoReturn:
        """Raise `UsageError` instead of argparse's default `self.exit(2, ...)`.

        An "unrecognized arguments" failure's message embeds the offending argv tokens
        verbatim, which may be a DSN with a password (m6 task-06 fix-1 I2; CONVENTIONS.md §7:
        `DATABASE_URL` is a secret) — that one message is replaced with a fixed, redacted string
        before being wrapped in `UsageError`, exactly like every other usage error, so the
        leftover argv is never rendered anywhere. This deliberately does NOT switch to
        argparse's own `SystemExit(2)`: `tests/test_triage_one.py::test_usage_error_missing_
        path_exit_1` documents that exit code `2` is reserved for `VerdictValidationError`
        (PRD §12 M0) on that CLI, so a usage error colliding with it would make the exit code
        ambiguous to a caller — every CLI built on this `Parser` (`scripts/seed_dev.py`,
        `evals/run.py`, `worker/triage_one.py`, `scripts/check_real_sessions.py`) keeps the
        existing "one stderr line, clean exit 1" contract; only the message content changes for
        this one failure shape.

        m6 task-06 fix-2 N1: argparse's OTHER value-bearing shape (`argument --limit: invalid
        int value: '<token>'`, from a `type=`-converting flag like `--limit`/`--concurrency`)
        also echoes its raw token, which could likewise be a DSN with a password. Every message
        is therefore also passed through an unconditional URL-like redaction (any
        `scheme://value` token becomes `<redacted>`) after the "unrecognized arguments"
        substitution — a no-op when the message carries no such token.

        Args:
            message: argparse's own description of the usage problem.

        Raises:
            UsageError: Always — this method never returns.
        """
        if message.startswith("unrecognized arguments"):
            message = "unrecognized argument(s) — values withheld"
        message = _URL_LIKE.sub("<redacted>", message)
        raise UsageError(f"{message} (see --help)")


def fail(code: str, message: str) -> int:
    """Print one flattened `error: <code>: <message>` line to stderr; never a traceback.

    Args:
        code: The stable wire code for this failure (e.g. `"config_error"`).
        message: A human-readable description; embedded newlines are collapsed so the line
            stays exactly one line, matching every other error path's shape.

    Returns:
        Always `1` — every caller of this helper is a `1`-exit-code path.
    """
    print(f"error: {code}: {' '.join(message.split())}", file=sys.stderr)
    return 1
