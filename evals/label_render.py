"""`evals.label_render`: renders a candidate for the human and the per-field label prompts (PRD
§6.6, §7.1, §13; m7 task-01, ruling R14).

Split out of `evals/label_tool.py` (m7 task-01 fix-1, review M9/M10): this module holds
`render_case` and the field-level prompt helpers, and imports no DB layer (`evals.candidates`,
`worker.summarize`, `core.schemas.*` only — no `evals.golden`, since nothing here constructs a
`GoldenCase`). `prompt_label` itself — the ONLY place in the repo that mints the human-labeled
marker — stays in `evals/label_tool.py`: the still-pinned `tests/test_label_tool.py::
test_only_label_tool_writes_labeled_by_human` asserts that exact assignment pattern (a `labeled_by`
name set to the literal string `human`) appears ONLY in the file `evals/label_tool.py`, by path,
and that pin predates and survives this split unedited — so the label-minting call site cannot
move here even though "prompts" might otherwise suggest it (m7 task-01 fix-1 implementer judgment
call).

Ruling R10 (review C1/C2, one root cause): `render_case` renders the alert only — nothing from a
candidate's sampling stratum, the cheap model's category, or any `sampled` field ever reaches the
human's screen. `prompt_category`'s numbered menu of all seven `STRATA_CATEGORIES` names IS
restored (ruling R16): the review found the ORIGINAL fix's removal of the menu over-corrected a
different bug — a menu whose text never varies by case cannot leak anything about the CURRENT
case's own label, so a static, always-identical menu is safe and is what the guide
(`docs/labeling-guide.md`) documents.

Ruling N2 (fix-3 re-review): `prompt_tags` offers the `injection` tag by re-scanning the
candidate's OWN alert (`evals.candidates.matches_injection_hint`), never from `candidate.stratum`
or a case's first-pass tags — so a re-reviewed case's tag offer never depends on what the author
typed last time.
"""

from __future__ import annotations

from typing import Protocol

from core.schemas.verdict import VerdictCategory
from evals.candidates import STRATA_CATEGORIES, Candidate, matches_injection_hint
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


class Quit(Exception):
    """Internal signal only: the human typed `"q"` — `label`/`rereview` stop the whole run."""


def render_case(candidate: Candidate) -> str:
    """Render `candidate` for the human: the summary, then every command/download/upload line
    verbatim, then every username tried — never logged, only ever passed to `Console.write`.

    Ruling R10: renders NOTHING from `candidate.stratum` or any other sampling-provenance field
    (the cheap model's category, by value) — only the alert itself.

    Args:
        candidate: The case to render.

    Returns:
        The multi-line text to show the labeler.
    """
    alert = candidate.alert
    summary = summarize_session(alert)
    lines = [
        f"case_id={candidate.case_id}",
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


def prompt_severity(console: Console) -> int | None:
    """Reads the severity prompt; returns `None` on `"s"` (skip), raises `Quit` on `"q"`."""
    while True:
        answer = console.read("severity (1-5, s=skip, q=quit): ").strip().lower()
        if answer == "s":
            return None
        if answer == "q":
            raise Quit
        try:
            severity = int(answer)
        except ValueError:
            console.write("severity must be 1-5, 's' or 'q' — try again")
            continue
        if 1 <= severity <= 5:
            return severity
        console.write("severity must be 1-5, 's' or 'q' — try again")


def prompt_category(console: Console) -> VerdictCategory:
    """Reads the category prompt: a numbered menu of `STRATA_CATEGORIES`, same order and text
    for every case (ruling R16). A menu whose text never varies by case cannot leak anything
    about the CURRENT case's original label — the actual C1 defect was fix-1 Part B's
    over-generalized pin, not the menu itself (review, fix-1 Part B judgment call 2); dropping the
    menu entirely would force the author to memorize seven category numbers by heart for two
    hundred labels, so it is restored here, documented identically in
    `docs/labeling-guide.md`."""
    console.write("category:")
    for i, name in enumerate(STRATA_CATEGORIES, start=1):
        console.write(f"  {i}. {name}")
    while True:
        answer = console.read(f"category (1-{len(STRATA_CATEGORIES)}): ").strip()
        try:
            index = int(answer) - 1
        except ValueError:
            console.write(f"enter a number 1-{len(STRATA_CATEGORIES)} — try again")
            continue
        if 0 <= index < len(STRATA_CATEGORIES):
            return STRATA_CATEGORIES[index]
        console.write(f"enter a number 1-{len(STRATA_CATEGORIES)} — try again")


def prompt_note(console: Console) -> str:
    """Reads the note prompt; re-prompts until it cites "6.6" (the PRD §6.6 rubric)."""
    while True:
        note = console.read('note (e.g. "§6.6 sev N: <evidence>"): ').strip()
        if "6.6" in note:
            return note
        console.write('note must cite "6.6" (the rubric row) — try again')


def prompt_tags(console: Console, candidate: Candidate) -> list[str]:
    """Reads the comma-separated tags prompt; offers `injection` when the candidate's OWN alert
    carries instruction-like text (ruling N2, review re-review): re-runs `matches_injection_hint`
    against `candidate.alert` directly, never `candidate.stratum` or a first-pass label — so a
    re-reviewed case's tag offer can never depend on whether it happened to be tagged `injection`
    last time (the sampler makes exactly this same check, PRD §10.6)."""
    hint = " ('injection' offered)" if matches_injection_hint(candidate.alert) else ""
    answer = console.read(f"tags (comma-separated{hint}): ").strip()
    return [tag.strip() for tag in answer.split(",") if tag.strip()]
