"""`evals.label_tool`: the author's v2 labeling CLI — the ONLY place `labeled_by: "human"` is ever
written (PRD §6.6, §7.1, §13; m7 task-01).

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
(severity OR category differs) — PRD §7.1: >10% means the rubric is ambiguous, not the labels.

`python -m evals.label_tool stats --golden <path>` prints the acceptance-walk table: rows per
category and severity band, the injection tag count, and the `labeled_by` breakdown.

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
from typing import Protocol

from core.cli import Parser, UsageError, fail
from core.schemas.alert import SessionAlert
from core.schemas.verdict import VerdictCategory
from evals.golden import GoldenCase, GoldenLabel, load_golden
from evals.sample import STRATA_CATEGORIES, Candidate
from worker.summarize import summarize_session

_COMMAND_EVENTS = {"cowrie.command.input", "cowrie.command.failed"}
_LOGIN_EVENTS = {"cowrie.login.failed", "cowrie.login.success"}


class Console(Protocol):
    """The labeling CLI's only I/O seam — injected in every test, wraps stdin/stdout for real
    use."""

    def write(self, text: str) -> None:
        """Print `text` (never logged; the only place attacker text may reach a screen)."""
        ...

    def read(self, prompt: str) -> str:
        """Print `prompt`, then return one line of typed input."""
        ...


class _RealConsole:
    """The real `Console`: wraps `print`/`input` for interactive use (`main`'s default)."""

    def write(self, text: str) -> None:
        print(text)

    def read(self, prompt: str) -> str:
        return input(prompt)


class _Quit(Exception):
    """Internal signal only: the human typed `"q"` — `label`/`rereview` stop the whole run."""


def render_case(candidate: Candidate) -> str:
    """Render `candidate` for the human: the summary, then every command/download/upload line
    verbatim, then every username tried — never logged, only ever passed to `Console.write`.

    Args:
        candidate: The case to render.

    Returns:
        The multi-line text to show the labeler.
    """
    alert = candidate.alert
    summary = summarize_session(alert)
    lines = [
        f"case_id={candidate.case_id} stratum={candidate.stratum}",
        f"session={summary.session_id} src_ip={summary.src_ip} sensor={summary.sensor}",
        f"connect_time={summary.connect_time.isoformat()} duration_ms={summary.duration_ms}",
        f"client_version={summary.client_version}",
        f"login_failed={summary.login_failed} login_success={summary.login_success}",
        f"command_count={summary.command_count} download_count={summary.download_count} "
        f"upload_count={summary.upload_count}",
        "",
        "Commands / downloads / uploads:",
    ]
    activity: list[str] = []
    for event in alert.events:
        if event.eventid in _COMMAND_EVENTS and event.input is not None:
            activity.append(f"  $ {event.input}")
        elif event.eventid == "cowrie.session.file_download" and event.url is not None:
            activity.append(f"  download {event.url} -> {event.outfile}")
        elif event.eventid == "cowrie.session.file_upload" and event.outfile is not None:
            activity.append(f"  upload -> {event.outfile}")
    lines.extend(activity if activity else ["  (none)"])

    usernames = [
        e.username for e in alert.events if e.eventid in _LOGIN_EVENTS and e.username is not None
    ]
    lines.append("")
    lines.append(
        f"Usernames tried ({len(usernames)}): {', '.join(usernames) if usernames else '(none)'}"
    )
    return "\n".join(lines)


def _prompt_severity(console: Console) -> int | None:
    """Reads the severity prompt; returns `None` on `"s"` (skip), raises `_Quit` on `"q"`."""
    while True:
        answer = console.read("severity (1-5, s=skip, q=quit): ").strip().lower()
        if answer == "s":
            return None
        if answer == "q":
            raise _Quit
        try:
            severity = int(answer)
        except ValueError:
            console.write("severity must be 1-5, 's' or 'q' — try again")
            continue
        if 1 <= severity <= 5:
            return severity
        console.write("severity must be 1-5, 's' or 'q' — try again")


def _prompt_category(console: Console) -> VerdictCategory:
    """Reads the category prompt: a numbered menu of `STRATA_CATEGORIES`."""
    console.write("category:")
    for i, name in enumerate(STRATA_CATEGORIES, start=1):
        console.write(f"  {i}. {name}")
    while True:
        answer = console.read("category (number): ").strip()
        try:
            index = int(answer) - 1
        except ValueError:
            console.write(f"enter a number 1-{len(STRATA_CATEGORIES)} — try again")
            continue
        if 0 <= index < len(STRATA_CATEGORIES):
            return STRATA_CATEGORIES[index]
        console.write(f"enter a number 1-{len(STRATA_CATEGORIES)} — try again")


def _prompt_note(console: Console) -> str:
    """Reads the note prompt; re-prompts until it cites "6.6" (the PRD §6.6 rubric)."""
    while True:
        note = console.read('note (e.g. "§6.6 sev N: <evidence>"): ').strip()
        if "6.6" in note:
            return note
        console.write('note must cite "6.6" (the rubric row) — try again')


def _prompt_tags(console: Console, candidate: Candidate) -> list[str]:
    """Reads the comma-separated tags prompt; offers `injection` for an injection-candidate."""
    hint = " ('injection' offered)" if candidate.stratum == "injection-candidate" else ""
    answer = console.read(f"tags (comma-separated{hint}): ").strip()
    return [tag.strip() for tag in answer.split(",") if tag.strip()]


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
    severity = _prompt_severity(console)
    if severity is None:
        return None
    category = _prompt_category(console)
    escalate = severity >= 4
    console.write(f"escalate: {escalate} (auto-derived, severity >= 4)")
    note = _prompt_note(console)
    tags = _prompt_tags(console, candidate)
    return GoldenCase(
        alert=candidate.alert,
        label=GoldenLabel(severity=severity, category=category, escalate=escalate),
        labeler_note=note,
        tags=tags,
        labeled_by="human",
        labeled_at=datetime.now(UTC),
    )


def _load_candidates(path: Path) -> list[Candidate]:
    """Read `evals.sample.write_candidates`'s output back into `Candidate` objects."""
    candidates: list[Candidate] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sampled = row["sampled"]
        candidates.append(
            Candidate(
                case_id=row["case_id"],
                alert_id=sampled["alert_id"],
                received_at=datetime.fromisoformat(sampled["received_at"]),
                stratum=sampled["stratum"],
                alert=SessionAlert.model_validate(row["alert"]),
            )
        )
    return candidates


def label(console: Console, candidates_path: Path, out_path: Path, *, start_at: int = 0) -> int:
    """Label every not-yet-labeled candidate in `candidates_path`, appending to `out_path`.

    Resumable: a candidate whose `case_id` is already present in `out_path` is skipped without
    prompting. `"q"` at any point stops the run, leaving `out_path` intact.

    Args:
        console: The injected I/O seam.
        candidates_path: The candidate file to label (`evals.sample.write_candidates`'s output).
        out_path: The golden file to append labeled cases to; created if absent.
        start_at: Skip this many candidates (in file order) before considering any of them.

    Returns:
        The number of cases newly labeled in this call.
    """
    candidates = _load_candidates(candidates_path)[start_at:]
    already_labeled: set[str] = set()
    if out_path.exists() and out_path.read_text().strip():
        already_labeled = {case.case_id for case in load_golden(out_path)}

    labeled_count = 0
    with out_path.open("a") as f:
        for candidate in candidates:
            if candidate.case_id in already_labeled:
                continue
            try:
                case = prompt_label(console, candidate)
            except _Quit:
                break
            if case is None:
                continue
            f.write(case.model_dump_json() + "\n")
            f.flush()
            labeled_count += 1
    return labeled_count


def _candidate_from_case(case: GoldenCase) -> Candidate:
    """A `Candidate` view of an already-labeled `GoldenCase`, for `rereview`'s re-prompt.

    `render_case` never touches `label`/`labeler_note`/`tags`, so building this view — even from
    a tagged `"injection"` case — never leaks the first label to the human (PRD §7.1: re-review
    without showing the first label).
    """
    stratum = "injection-candidate" if "injection" in case.tags else case.label.category
    return Candidate(
        case_id=case.case_id,
        alert_id=case.case_id,
        received_at=case.labeled_at or datetime.now(UTC),
        stratum=stratum,
        alert=case.alert,
    )


def rereview(
    console: Console, golden_path: Path, out_path: Path, *, fraction: float, seed: int
) -> float:
    """Re-label a seeded `fraction` of `golden_path` from scratch and report the disagreement rate.

    PRD §7.1: a re-review a week after the first pass; a rate over 10% means the rubric itself is
    ambiguous, not merely a labeler slip. Never shows the first label before the second is typed.

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
        except _Quit:
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

    rate = disagreements / len(reviewed) if reviewed else 0.0
    pct = rate * 100
    if rate > 0.10:
        console.write(
            f"disagreement {pct:.1f} % ({disagreements}/{len(reviewed)}) — > 10 % — fix the "
            "rubric and relabel (PRD §7.1)"
        )
    else:
        console.write(f"disagreement {pct:.1f} % ({disagreements}/{len(reviewed)}) — rubric OK")
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
        `OSError`/`ValueError` while reading or writing a file.
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
        return fail("io_error", f"{type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
