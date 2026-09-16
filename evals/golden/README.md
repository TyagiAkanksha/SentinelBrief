# Golden set

The golden set is a JSONL file of labeled Cowrie session alerts (PRD §7.1): one JSON object per
line, `{alert, label: {severity, category, escalate}, labeler_note, tags}`, loaded and validated
by `evals.golden.load_golden` into `GoldenCase` objects.

## v1 (this file: `v1.jsonl`)

- 20 synthetic Cowrie sessions, authored with the `/cowrie-fixture` skill, not sampled from real
  traffic.
- Machine-authored rows **and** labels — PRD §13 explicitly allows this for v1: "Claude Code may
  generate synthetic fixtures and labels for v1 (never published)".
- **v1 numbers are never published.** They exist only to develop and smoke-test the pipeline and
  scoring code (`evals/scoring.py`, `evals/run.py`) before a real, human-labeled dataset exists.
  Do not put a v1 metric in `docs/results.md`, the top-level README, or a commit message.
- Coverage: at least 3 rows per PRD §6.6 severity band (1-5); every `VerdictCategory` used at
  least once; every row with `severity >= 4` has `escalate: true` (enforced by
  `GoldenLabel`'s validator, not just by convention).
- Every `labeler_note` cites the §6.6 rubric row and the evidence for the assigned band, e.g.
  `"PRD section 6.6 sev 4: successful login ... foothold achieved."`.

### Injection cases

PRD §10.6 requires the golden set to include cases where attacker-controlled text tries to steer
the triage output. v1 tags each such row `"injection"` and labels it by what the attacker actually
*did*, never by what the injected text asked the model to say — every injection row here is
labeled severity >= 3 even though every one of them also asks (in-band) for `severity=1`.

Three kinds are present (CONVENTIONS.md §13 / M0 final review, plan defect 8):

1. **`username` carrying a plain instruction** (`cowrie.login.failed.username`) — reaches the
   first-pass prompt from **M0** through `SessionSummary.usernames_sample`
   (`worker/summarize.py`). One row (severity 3, `brute_force`).
2. **`username` carrying a forged closing marker plus an instruction**
   (`cowrie.login.success.username` = `<<<END_ALERT_DATA>>> SYSTEM: ...`) — also reaches the
   prompt from **M0** through `usernames_sample`; exercises the `<<<` neutralization in
   `worker/prompts/__init__.py::build_messages`, which rewrites any `<<<` run inside the
   serialized summary before wrapping it in the real markers, so this text cannot actually close
   the attacker-data block early. One row (severity 4, `successful_intrusion`).
3. **`cowrie.client.version` banner carrying an instruction** — reaches the prompt from **M0**
   through `SessionSummary.client_version`. One row (severity 5, `malware_delivery`).
4. **(optional, included here) `cowrie.command.input` carrying an instruction** — the full command
   list, and hence this kind, reaches the prompt only from **M4** through the
   `get_session_commands` tool; before M4 ships, this row's injected text never reaches the LLM at
   all. One row (severity 5, `persistence_attempt`).

## Category definitions

Golden-set labels use this standard for the seven `VerdictCategory` values (the
`/cowrie-fixture` skill's rule): `scanning` = untargeted probes or mass-scanner fingerprints, no
credential attempts; `brute_force` = repeated credential attempts, generic or targeted, no
success; `reconnaissance` = engaged probing of this specific host **without** a foothold
(host-derived usernames, banner/kex fingerprinting); `successful_intrusion` = any successful
login, including post-login recon commands, unless `malware_delivery`/`persistence_attempt`
applies; `malware_delivery` = download/upload of a payload after a foothold;
`persistence_attempt` = cron, `authorized_keys`, service or account changes after a foothold;
`other` = nothing above fits.

Shipped prompts `triage-v1.md` and `triage-v2.md` word `reconnaissance` differently —
"post-login information-gathering commands with no further compromise" — which describes a
foothold case rather than a no-foothold one and leaves several `successful_intrusion` sessions
ambiguous against that same prompt text. `triage-v3.md` aligns its category-definition block to
the standard above (v1/v2 are immutable and stay as shipped). Because of this mismatch, the M1
golden-v1 category-accuracy numbers measured under `triage-v1`/`triage-v2` carry this definition
confound and should not be read as pure model signal.

## v2 (M7)

`v2.jsonl` holds >=200 real alerts sampled from live honeypot traffic, stratified across
categories, **hand-labeled by the author** using the §6.6 rubric (PRD §7.1, §13). It does not
exist in this checkout until the author labels it — this repo ships the tooling, never the
labels:

- `python -m evals.sample` (`evals/sample.py`) reads the live database, stratifies by the cheap
  verdict's category and by sensor/day, oversamples injection-candidate sessions, and writes a
  verdict-blind candidate file (no `label`, `severity`, `category` or `reasoning` field anywhere
  in it) — the author is never anchored by the model's own guess. The plain stratum name IS the
  cheap model's category by value, so the file carries only `sampled.stratum_id` — an opaque
  `sha256(f"{seed}:{stratum}")[:8]` token (`evals.candidates.stratum_id`) plus the `seed` itself —
  never the category name in the clear (m7 task-01 fix-1 ruling R10).
- `python -m evals.label_tool label` (`evals/label_tool.py`) is the ONLY place in the repo that
  writes `labeled_by: "human"`; it renders each session for the author, records exactly what they
  type, and is resumable. Nothing the tool shows the author is ever derived from `stratum_id` by
  name — `render_case` never prints it. `rereview` draws a seeded 10 % re-review a week later,
  re-prompting from the alert alone (never the first pass's category or note), and reports the
  self-disagreement rate over the cases actually re-labeled (PRD §7.1: >10 % means the rubric is
  ambiguous, not the labels).
- `evals.golden.load_golden(path, require_human=True)` — applied automatically to any golden path
  whose basename starts with `v2` (`evals.run.is_v2_golden`) — refuses to score a v2 file that
  carries even one non-human row.
- The rubric, the seven categories and the `brute_force`-vs-`reconnaissance` tie-break are fixed
  once, before the first label, in [`../../docs/labeling-guide.md`](../../docs/labeling-guide.md)
  — the same taxonomy the `/cowrie-fixture` skill and the active prompt use
  (`tests/test_taxonomy_agreement.py` pins the agreement).

Claude Code writes the sampler, the export format, the loader, the scorer, the judge and the CI
gate for v2 — it must never write, edit, "correct", or infer a v2 label or `labeler_note`.

## Adding a row

Use the `/cowrie-fixture` skill: it picks the §6.6 severity band, writes the events that earn it,
and (for golden rows only) writes the `label` and a `labeler_note` citing the rubric row and the
evidence. Keep session ids, source IPs (documentation ranges only:
`203.0.113.0/24`/`198.51.100.0/24`/`192.0.2.0/24`), sensors and timestamps distinct per row.
