"""The one copy of the CLI presentation helpers shared by every SentinelBrief CLI
(CONVENTIONS.md §4/§9; SUGGESTIONS.md t6-M6) — m5 task-05.

`worker/triage_one.py`, `evals/run.py` and `scripts/seed_dev.py` each carried a byte-identical
copy of `UsageError`/`_Parser`/`_fail` before this task; all three now import from here instead.
`scripts/` sits outside mypy's `strict` file list (CONVENTIONS.md §9) but may still import
`core.cli` like any other module.
"""

from __future__ import annotations

import argparse
import sys
from typing import NoReturn

from core.errors import SentinelBriefError


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

        Args:
            message: argparse's own description of the usage problem.

        Raises:
            UsageError: Always — this method never returns.
        """
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
