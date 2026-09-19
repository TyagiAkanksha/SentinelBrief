"""`evals.label_tool`: the author's v2 labeling CLI — file I/O and orchestration around
`evals.label_render` (PRD §6.6, §7.1, §13; m7 task-01, ruling R14).

`python -m evals.label_tool label --candidates <path> --out <path> [--start-at N]` walks a
candidate file (`evals.sample.write_candidates`'s output) one case at a time: it renders the
session summary and the full command/download/upload list to the terminal (`render_case`) — the
human sees attacker text on purpose, and it never reaches a log — prompts for severity, category,
note and tags (escalate is derived, never asked), validates against the PRD §6.6 rubric, and
appends a `GoldenCase` row with `labeled_by="human"` and `labeled_at=now(UTC)` to the output file.
It is resumable (a case already present in the output file is skipped) and supports `"s"` (skip
this case) / `"q"` (quit; the file so far is left intact) at the severity prompt.

`python -m evals.label_tool rereview --golden <path> --fraction 0.10 --seed <N> --out <path>`
draws a seeded fraction of an existing golden file, re-labels each case from scratch (without
showing the first label) through the same `prompt_label`, and reports the disagreement rate
(severity OR category differs, divided by the number of cases ACTUALLY re-labeled — a quit or
skip mid-review must never dilute the rate) — PRD §7.1: >10% means the rubric is ambiguous, not
the labels.

`python -m evals.label_tool stats --golden <path>` prints the acceptance-walk table: rows per
category and severity band, the injection tag count, and the `labeled_by` breakdown.

Ruling R14 (review M9): this module imports no DB layer — `sqlalchemy`, `core.db`, `core.models`
and `evals.sample` are all forbidden here (pinned by an AST test). `render_case` is re-exported
from `evals.label_render` unchanged, so every existing `from evals.label_tool import ...` keeps
working; `Candidate`/`STRATA_CATEGORIES`/`stratum_id` come from the DB-free `evals.candidates`,
never `evals.sample`. `prompt_label` itself — the ONLY place in the repo that writes
`labeled_by="human"` — is defined HERE, not in `evals.label_render`: the still-pinned
`tests/test_label_tool.py::test_only_label_tool_writes_labeled_by_human` asserts that exact
literal text appears only in the file `evals/label_tool.py`.

Every test drives this module through the injected `Console` (never real stdin/stdout) feeding
scripted answers (CONVENTIONS.md §10: mock only the seam). Nothing here ever fabricates a label —
`prompt_label` returns exactly what was typed, or `None` on skip.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from core.cli import Parser, UsageError, fail
from core.schemas.alert import SessionAlert
from evals.candidates import STRATA_CATEGORIES, Candidate, stratum_id
from evals.golden import GoldenCase, GoldenLabel, load_golden
from evals.label_render import (
    Console,
    Quit,
    prompt_category,
    prompt_note,
    prompt_severity,
    prompt_tags,
    render_case,
)

__all__ = [
    "Console",
    "label",
    "main",
    "prompt_label",
    "render_case",
    "rereview",
    "stats",
]

_KNOWN_STRATA = (*STRATA_CATEGORIES, "injection-candidate", "unverdicted")


def prompt_label(console: Console, candidate: Candidate) -> GoldenCase | None:
    """Ask the human for one label; the ONLY place in the repo that writes `labeled_by="human"`.

    Renders `candidate` first, then asks severity, category, note and tags in turn; `escalate`
    is derived (severity >= 4), never asked. `"s"` at the severity prompt returns `None`
    (skip this case); `"q"` raises an internal quit signal the caller (`label`/`rereview`)
    catches to stop the whole run, leaving the output file intact.

    Args:
        console: The injected I/O seam.
        candidate: The case to label.

    Returns:
        The typed `GoldenCase` (`labeled_by="human"`, `labeled_at=now(UTC)`), or `None` if the
        human skipped this case.
    """
    console.write(render_case(candidate))
    severity = prompt_severity(console)
    if severity is None:
        return None
    category = prompt_category(console)
    escalate = severity >= 4
    console.write(f"escalate: {escalate} (auto-derived, severity >= 4)")
    note = prompt_note(console)
    tags = prompt_tags(console, candidate)
    return GoldenCase(
        alert=candidate.alert,
        label=GoldenLabel(severity=severity, category=category, escalate=escalate),
        labeler_note=note,
        tags=tags,
        labeled_by="human",
        labeled_at=datetime.now(UTC),
    )


class _RealConsole:
    """The real `Console`: wraps `print`/`input` for interactive use (`main`'s default)."""

    def write(self, text: str) -> None:
        print(text)

    def read(self, prompt: str) -> str:
        return input(prompt)


def _load_candidates(path: Path) -> list[Candidate]:
    """Read `evals.sample.write_candidates`'s output back into `Candidate` objects.

    `sampled.stratum_id` is opaque on disk (ruling R10); `stratum` is reconstructed in memory
    only, by recomputing `stratum_id(name, seed)` for the finite set of known stratum names and
    matching against each row's own `sampled.seed` — never rendered (`render_case` never touches
    `candidate.stratum`), and, since ruling N2, no longer read by `prompt_tags` either (its
    `injection` offer comes from the alert directly); kept here purely as accurate provenance on
    the `Candidate` object.

    Every field this function reads is guarded (review N1): a row that is not a JSON object, or
    is missing `case_id`/`alert`/`sampled`/`sampled.seed`/`sampled.stratum_id`/
    `sampled.alert_id`/`sampled.received_at` — a truncated or pre-R10 candidates file, e.g. from a
    partial copy-back over SSM — raises `ValueError` naming the row number and the missing key
    only, never the row's own (possibly attacker-derived) content, so it surfaces through `main`'s
    existing `io_error` path instead of an unguarded `KeyError`/`TypeError` traceback.

    Raises:
        ValueError: A row isn't a JSON object, or is missing a required key.
    """
    candidates: list[Candidate] = []
    for row_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"candidate row {row_number}: not a JSON object")
        try:
            case_id = row["case_id"]
            alert_data = row["alert"]
            sampled = row["sampled"]
            if not isinstance(sampled, dict):
                raise ValueError(f"candidate row {row_number}: sampled is not a JSON object")
            seed = sampled["seed"]
            stratum_id_value = sampled["stratum_id"]
            alert_id = sampled["alert_id"]
            received_at = sampled["received_at"]
        except KeyError as e:
            raise ValueError(f"candidate row {row_number}: missing {e.args[0]!r}") from e
        id_to_name = {stratum_id(name, seed): name for name in _KNOWN_STRATA}
        stratum = id_to_name.get(stratum_id_value, "unknown")
        candidates.append(
            Candidate(
                case_id=case_id,
                alert_id=alert_id,
                received_at=datetime.fromisoformat(received_at),
                stratum=stratum,
                alert=SessionAlert.model_validate(alert_data),
            )
        )
    return candidates


def label(console: Console, candidates_path: Path, out_path: Path, *, start_at: int = 0) -> int:
    """Label every not-yet-labeled candidate in `candidates_path`, appending to `out_path`.

    Resumable: a candidate whose `case_id` is already present in `out_path` is skipped without
    prompting. `"q"` at any point stops the run, leaving `out_path` intact. If `out_path` already
    exists and does not end with a trailing newline (e.g. a hand-edit or a merge dropped it), one
    is written before appending, so a labeling sitting never silently concatenates onto the
    previous row.

    Args:
        console: The injected I/O seam.
        candidates_path: The candidate file to label (`evals.sample.write_candidates`'s output).
        out_path: The golden file to append labeled cases to; created if absent.
        start_at: Skip this many candidates (in file order) before considering any of them.

    Returns:
        The number of cases newly labeled in this call.
    """
    candidates = _load_candidates(candidates_path)[start_at:]
    existing_text = out_path.read_text() if out_path.exists() else ""
    already_labeled: set[str] = set()
    if existing_text.strip():
        already_labeled = {case.case_id for case in load_golden(out_path)}
    if existing_text and not existing_text.endswith("\n"):
        with out_path.open("a") as f:
            f.write("\n")

    labeled_count = 0
    with out_path.open("a") as f:
        for candidate in candidates:
            if candidate.case_id in already_labeled:
                continue
            try:
                case = prompt_label(console, candidate)
            except Quit:
                break
            if case is None:
                continue
            f.write(case.model_dump_json() + "\n")
            f.flush()
            labeled_count += 1
    return labeled_count


def _candidate_from_case(case: GoldenCase) -> Candidate:
    """A `Candidate` view of an already-labeled `GoldenCase`, for `rereview`'s re-prompt.

    Ruling R10 (review C1): `stratum` is NEVER derived from `case.label.category` — that would
    leak the first pass's category even though `render_case` itself never prints it. Ruling N2
    (fix-3 re-review): `stratum` is ALSO never derived from `case.tags` any more — `prompt_tags`
    now decides the `injection` offer by re-scanning `candidate.alert` directly
    (`evals.candidates.matches_injection_hint`), so a fixed placeholder here is enough; a
    re-reviewed case's tag offer can no longer depend on whether it was tagged `injection` last
    time.
    """
    return Candidate(
        case_id=case.case_id,
        alert_id=case.case_id,
        received_at=case.labeled_at or datetime.now(UTC),
        stratum="rereview",
        alert=case.alert,
    )


def rereview(
    console: Console, golden_path: Path, out_path: Path, *, fraction: float, seed: int
) -> float:
    """Re-label a seeded `fraction` of `golden_path` from scratch and report the disagreement rate.

    PRD §7.1: a re-review a week after the first pass; a rate over 10% means the rubric itself is
    ambiguous, not merely a labeler slip. Never shows the first label before the second is typed
    (`_candidate_from_case` carries no label field at all). The rate divides by the number of
    cases actually re-labeled, not the sampled count — a quit or skip mid-review must never dilute
    it toward "rubric OK" (review I1).

    Args:
        console: The injected I/O seam.
        golden_path: The golden file to re-review.
        out_path: Where to write one `{"case_id", "first", "second"}` line per re-reviewed case.
        fraction: The fraction of rows to re-review (e.g. `0.10`).
        seed: The seed for the sample draw.

    Returns:
        The disagreement rate (severity OR category differs), in `[0.0, 1.0]`.
    """
    cases = load_golden(golden_path)
    rng = random.Random(seed)
    k = round(fraction * len(cases))
    reviewed = rng.sample(cases, k) if k < len(cases) else list(cases)

    disagreements = 0
    rows: list[dict[str, object]] = []
    for case in reviewed:
        candidate = _candidate_from_case(case)
        try:
            second = prompt_label(console, candidate)
        except Quit:
            break
        if second is None:
            continue
        disagreed = (
            second.label.severity != case.label.severity
            or second.label.category != case.label.category
        )
        if disagreed:
            disagreements += 1
        rows.append(
            {
                "case_id": case.case_id,
                "first": case.model_dump(mode="json"),
                "second": second.model_dump(mode="json"),
            }
        )

    out_path.write_text("\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""))

    relabeled = len(rows)
    rate = disagreements / relabeled if relabeled else 0.0
    pct = rate * 100
    if rate > 0.10:
        console.write(
            f"disagreement {pct:.1f} % ({disagreements}/{relabeled}) — > 10 % — fix the "
            "rubric and relabel (PRD §7.1)"
        )
    else:
        console.write(f"disagreement {pct:.1f} % ({disagreements}/{relabeled}) — rubric OK")
    return rate


def stats(golden_path: Path) -> str:
    """The acceptance-walk table: rows per category/severity, injection tag count, labeled_by.

    Args:
        golden_path: The golden file to summarize.

    Returns:
        A multi-line human-readable string with the counts above.
    """
    cases = load_golden(golden_path)

    category_counts: dict[str, int] = {}
    severity_counts: dict[int, int] = {}
    tag_counts: dict[str, int] = {}
    labeled_by_counts: dict[str, int] = {}
    for case in cases:
        category_counts[case.label.category] = category_counts.get(case.label.category, 0) + 1
        severity_counts[case.label.severity] = severity_counts.get(case.label.severity, 0) + 1
        for tag in case.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        key: str = case.labeled_by or "none"
        labeled_by_counts[key] = labeled_by_counts.get(key, 0) + 1

    lines = [f"golden-set stats: {len(cases)} row(s)", "", "category counts:"]
    for category in STRATA_CATEGORIES:
        if category in category_counts:
            lines.append(f"  {category}: {category_counts[category]}")

    lines.append("")
    lines.append("severity counts:")
    for severity in sorted(severity_counts):
        lines.append(f"  severity {severity}: {severity_counts[severity]}")

    lines.append("")
    lines.append("tag counts:")
    if tag_counts:
        for tag in sorted(tag_counts):
            lines.append(f"  {tag}: {tag_counts[tag]}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("labeled_by breakdown:")
    for key in sorted(labeled_by_counts):
        lines.append(f"  {key}: {labeled_by_counts[key]}")

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, console: Console | None = None) -> int:
    """The `label` / `rereview` / `stats` CLI.

    Args:
        argv: Command-line arguments (excluding the program name); `None` reads `sys.argv[1:]`.
        console: The `Console` to use; `None` builds the real one (wraps stdin/stdout).

    Returns:
        `0` on success; `1` via `core.cli.fail(e.code, ...)` on a malformed command line or an
        `OSError`/`ValueError` while reading or writing a file (`io_error`: the exception's CLASS
        NAME only, never its text — a malformed row's validation error can embed the row's own
        attacker-reachable content, review M3).
    """
    parser = Parser(prog="python -m evals.label_tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    label_parser = subparsers.add_parser("label")
    label_parser.add_argument("--candidates", type=Path, required=True)
    label_parser.add_argument("--out", type=Path, required=True)
    label_parser.add_argument("--start-at", type=int, default=0)

    rereview_parser = subparsers.add_parser("rereview")
    rereview_parser.add_argument("--golden", type=Path, required=True)
    rereview_parser.add_argument("--fraction", type=float, default=0.10)
    rereview_parser.add_argument("--seed", type=int, required=True)
    rereview_parser.add_argument("--out", type=Path, required=True)

    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument("--golden", type=Path, required=True)

    try:
        args = parser.parse_args(argv)
    except UsageError as e:
        return fail(e.code, str(e))

    active_console: Console = console if console is not None else _RealConsole()

    try:
        if args.command == "label":
            n = label(active_console, args.candidates, args.out, start_at=args.start_at)
            active_console.write(f"labeled {n} case(s)")
        elif args.command == "rereview":
            rereview(active_console, args.golden, args.out, fraction=args.fraction, seed=args.seed)
        else:
            active_console.write(stats(args.golden))
    except (OSError, ValueError) as e:
        return fail("io_error", f"{type(e).__name__}: could not read the file")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
