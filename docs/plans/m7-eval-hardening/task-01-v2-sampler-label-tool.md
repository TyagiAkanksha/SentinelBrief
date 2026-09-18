---
id: task-01
milestone: m7-eval-hardening
depends_on: []
status: planned
spec: PRD.md §7.1 (v2 = ≥200 real alerts sampled from live traffic, stratified across categories, hand-labeled by the author with the §6.6 rubric; rows `{alert, label, labeler_note}`; 10 % re-review a week later, >10 % self-disagreement means the rubric is ambiguous), §7.5 (v2 published in-repo as a labeled dataset), §10.6 (≥5 injection cases in the golden set), §13 ("Golden set v2 labels — must be human work"), §6.6 (the rubric); `docs/plans/m7-eval-hardening.md` Global Constraints (no agent writes/edits/infers a v2 label; `labeled_by: human` on every row; ONE category taxonomy across the labeling guide, the `/cowrie-fixture` skill and the active prompt, incl. the `brute_force`-vs-`reconnaissance` tie-break, BEFORE the author labels a row); `.claude/rules/evals.md`; `CONVENTIONS.md` §10, §13; M6 task-06 review PC4 (this sampler needs its own query with per-alert identity and verdicts)
---

# task-01 — `evals/sample.py` (stratified v2 candidate export from the live DB, verdicts hidden), `evals/label_tool.py` (the author's labeling + 10 % re-review CLI; the ONLY writer of `labeled_by: human`), the labeling guide, and the v2 loader contract

## Goal

The author needs, on day one of M7, a file of real sessions to label and a tool that makes labeling
fast, consistent and auditable — and the repo needs a hard guarantee that no code path ever
fabricates a v2 label. `evals/sample.py` reads the production database (read-only; the same
`--database-url`/`--schema` seam as `scripts/check_real_sessions.py`), joins each alert with its
latest verdict, stratifies by the cheap verdict's category and by sensor/day, oversamples the
rare strata and the "injection-candidate" stratum (sessions whose usernames or commands carry
instruction-like text), and writes a candidate JSONL that carries the raw `SessionAlert`, a stable
`case_id`, the sampling provenance — and NO verdict fields, so the labeler is never anchored by
the model. `evals/label_tool.py` walks that file: for each case it renders the session summary
and the full command list to the terminal (the human sees attacker text — that is the job; the
tool never logs it), prompts for severity, category, escalate, note and tags, validates against
the §6.6 rubric (severity ≥ 4 ⇒ escalate), and appends a `GoldenCase` row with `labeled_by:
"human"` and `labeled_at` to `evals/golden/v2.jsonl`; it is resumable, and its `rereview` mode
draws a seeded 10 % sample for the second pass and prints the disagreement rate. The loader gains
`require_human=True` so `evals.run` refuses a v2 file with any non-human row. `docs/labeling-guide.md`
fixes the taxonomy the guide, the `/cowrie-fixture` skill and `triage-v4.md` must share
(tie-break included) before the first label is typed. Every test drives the tools through
injected stdin/stdout and a throwaway schema; no test — and no module under `evals/` — ever
constructs a v2 label from anything but typed input.

## Context (read ONLY these)

- `PRD.md` §6.6, §7.1, §7.5, §10.6, §13.
- `docs/plans/m7-eval-hardening.md` — Global Constraints.
- `.claude/rules/evals.md`; `CONVENTIONS.md` §10, §13.
- Code you build on: `evals/golden/__init__.py` (`GoldenLabel`, `GoldenCase`, `load_golden` —
  the v1 loader; `GoldenCase.tags`, `labeler_note` min length 10), `evals/golden/v1.jsonl` (row
  shape), `scripts/check_real_sessions.py` (M6 task-06: the DB-reading script shape —
  `--database-url`/`--schema`, `core.db.make_engine`, `core.cli.fail`, read-only, names-only
  hygiene — copy its seam; this task's tools print attacker text to the HUMAN on purpose but never
  to a log or a file other than the candidate/golden JSONL), `core/models/alerts.py` +
  `core/models/verdicts.py` (`AlertRow.raw`, `VerdictRow.category/severity/created_at`),
  `worker/summarize.py::summarize_session` (the summary the labeler sees first), the
  `/cowrie-fixture` skill (`.claude/skills/cowrie-fixture/SKILL.md` category paragraph),
  `worker/prompts/triage-v4.md` lines 33–38 (the active category definitions), `tests/test_golden.py`,
  `tests/test_seed_dev.py` (DB-backed script tests), `tests/helpers.py::seed_alert`.

## Files

- Create: `evals/sample.py`, `evals/label_tool.py`, `docs/labeling-guide.md`,
  `evals/golden/README.md` (v2 section), `evals/golden/.gitkeep`? — no: `evals/golden/v2.jsonl` is
  created ONLY by the label tool from the author's input; it does not exist at the end of this task.
- Create (test-author): `tests/test_sample.py`, `tests/test_label_tool.py`, `tests/test_golden_v2.py`,
  `tests/test_taxonomy_agreement.py`
- Modify: `evals/golden/__init__.py` (`GoldenCase.labeled_by`, `labeled_at`; `load_golden(path, *,
  require_human=False)`), `evals/run.py` (`require_human=True` when the golden path's basename starts
  with `v2`), `.claude/skills/cowrie-fixture/SKILL.md` (the category paragraph gains the tie-break
  sentence — the ONE taxonomy), `README.md` (an "Evals — labeling v2" paragraph pointing at the
  guide), `.env.example` (nothing — no new Setting), `.gitignore` (`evals/golden/v2-candidates.jsonl`
  and `evals/golden/v2-rereview.jsonl` are gitignored working files; `v2.jsonl` is tracked)

## Interfaces

- **Consumes:** `AlertRow`/`VerdictRow`; `SessionAlert`; `summarize_session`; `GoldenCase`.
- **Produces exactly:**

  ```python
  # evals/sample.py — python -m evals.sample --database-url … [--schema …] --n 240 --seed 20260914 --out evals/golden/v2-candidates.jsonl [--since 2026-09-12] [--exclude evals/golden/v2.jsonl]
  STRATA_CATEGORIES = ("scanning", "brute_force", "reconnaissance", "successful_intrusion", "malware_delivery", "persistence_attempt", "other")
  INJECTION_HINT = re.compile(r"ignore (all |previous |prior )?instructions|system prompt|as an ai|severity ?[:=] ?[1-5]|rate (this|it) (as )?(low|benign|1)", re.I)   # ruling R11: no bare `assistant`; `severity` needs `:` or `=` — a hint for the stratum only, never a label
  @dataclass(frozen=True)
  class Candidate: case_id: str; alert_id: str; received_at: datetime; stratum: str; alert: SessionAlert     # stratum = "<cheap category>" | "injection-candidate" | "unverdicted"
  async def sample(session_factory, *, n: int, seed: int, since: datetime | None, exclude_case_ids: frozenset[str]) -> list[Candidate]
      # newest-first query: alerts LEFT JOIN the latest verdict per alert (a subquery on max(created_at)); status in ("triaged", "failed")
      # stratum = "injection-candidate" if INJECTION_HINT matches any event's username/input; else the cheap verdict's category; else "unverdicted"
      # target per stratum = max(5, n // 8) for every category stratum that has members, ALL injection-candidates up to n // 4, the remainder filled round-robin by (sensor, day) so no single day dominates; random.Random(seed) draws within a stratum; deterministic for (seed, DB state)
  def write_candidates(path: Path, candidates: Sequence[Candidate], *, seed: int) -> int   # (ruling R10: the seed is recorded)
      # one JSON object per line: {"case_id", "alert": alert.model_dump(mode="json"), "sampled": {"alert_id", "received_at", "stratum_id", "seed"}} — NO "label", NO verdict fields, NO model output (hidden by design)
      # ruling R10: `stratum_id` is OPAQUE — `hashlib.sha256(f"{seed}:{stratum}".encode()).hexdigest()[:8]` — because the plain stratum IS the cheap model's category by value.
      # `stratum_id(stratum: str, seed: int) -> str` is a pure function in `evals/sample.py`; the `stats` command recovers the mapping by recomputing it for the known strata names. `render_case` renders NOTHING from `sampled`, ever.
  def main(argv: Sequence[str] | None = None) -> int   # exit 0; 1 via core.cli.fail on no URL / DB error (class name only)

  # evals/golden/__init__.py
  class GoldenCase(BaseModel):
      alert: SessionAlert; label: GoldenLabel; labeler_note: Annotated[str, Field(min_length=10)]; tags: list[str] = []
      labeled_by: Literal["human"] | None = None      # v2 rows: always "human"; v1 rows: None
      labeled_at: datetime | None = None
  def load_golden(path: Path, *, require_human: bool = False) -> list[GoldenCase]   # require_human → ValueError("row N is not human-labeled") for any row without labeled_by == "human"

  # evals/label_tool.py — python -m evals.label_tool label --candidates evals/golden/v2-candidates.jsonl --out evals/golden/v2.jsonl [--start-at N]
  #                       python -m evals.label_tool rereview --golden evals/golden/v2.jsonl --fraction 0.10 --seed 20260921 --out evals/golden/v2-rereview.jsonl
  #                       python -m evals.label_tool stats --golden evals/golden/v2.jsonl
  class Console(Protocol): def write(self, text: str) -> None; def read(self, prompt: str) -> str        # injected in tests; the real one wraps sys.stdout / input()
  def render_case(candidate: Candidate) -> str          # the summary (summarize_session), then EVERY command/download/upload line verbatim, then the usernames tried — for the human's eyes; never logged
  def prompt_label(console: Console, candidate: Candidate) -> GoldenCase | None
      # asks: severity (1-5), category (a numbered menu of STRATA_CATEGORIES), escalate (auto-true when severity >= 4 — shown, not asked), note (min 10 chars, must cite "6.6" — re-prompts), tags (comma list; "injection" offered when the stratum is injection-candidate); "s" skips the case; "q" quits (resumable)
      # returns GoldenCase(..., labeled_by="human", labeled_at=now(UTC)) — the ONLY place in the repo that writes labeled_by="human"
  def label(console, candidates_path, out_path, *, start_at: int = 0) -> int          # appends one JSON line per labeled case; skips case_ids already present in out_path (resumable); returns labeled count
  def rereview(console, golden_path, out_path, *, fraction: float, seed: int) -> float  # seeded sample of round(fraction*n) rows, re-prompts WITHOUT showing the first label, writes {case_id, first: {...}, second: {...}} lines, returns the disagreement rate (severity OR category differs) and prints "disagreement 7.5 % (3/40) — rubric OK" or "... > 10 % — fix the rubric and relabel (PRD §7.1)"
  def stats(golden_path) -> str                          # strata table: rows per category / severity band, injection tag count, labeled_by breakdown — the acceptance-walk table
  def main(argv: Sequence[str] | None = None, *, console: Console | None = None) -> int
  ```

  `docs/labeling-guide.md`: the §6.6 rubric table verbatim; the seven categories with the
  `triage-v4.md` definitions verbatim; the tie-break: *"credential attempts using usernames
  derived from THIS host (its hostname, banner, prior recon) with no success → `reconnaissance`;
  generic or list-based sprays with no success → `brute_force`; any success → `successful_intrusion`
  unless malware/persistence follows"*; escalate = severity ≥ 4 always; the injection tag rule
  (tag `injection` when any username/command carries instruction-like text, and label by what the
  attacker actually DID, not what the text asks for); the note format (`"§6.6 sev N: <evidence>"`);
  the workflow (sample → label in sittings, `q` to pause → `stats` → a week later `rereview` →
  fix the rubric + relabel if > 10 %); what the tool never shows (the model's verdict).

  Taxonomy agreement (Global Constraint): the guide, `.claude/skills/cowrie-fixture/SKILL.md`'s
  category paragraph and `worker/prompts/triage-v4.md` lines 33–38 carry the SAME seven names and
  the same tie-break sentence — pinned by `tests/test_taxonomy_agreement.py` (it extracts the
  category names from all three and asserts set-equality, and greps the tie-break sentence's key
  phrase "derived from this host" in the guide and the skill; the prompt's `reconnaissance` line
  already says "host-derived usernames").

## Rulings from the task-01 review (2026-09-16)

- **R10 — verdict blindness is a property of the FILE and the rendering, not only of key names.**
  `sampled.stratum_id` is the opaque token above; `write_candidates` takes `seed`; `render_case`
  never prints anything from `sampled`, and `rereview` re-prompts from the alert alone — the
  first-pass label is never shown or hinted (review C1/C2: the shipped tool printed
  `stratum=<category>`).
- **R11 — `INJECTION_HINT` tightened** (bare `assistant` dropped; `severity` needs `:`/`=`); it only
  shapes the sample, the human decides the tag.
- **R12 — the Verify block's `grep -c labeled_by … → 0` was unsatisfiable** (annotations, docstrings
  and the loader's comparison all contain the word); the human-only guarantee is the AST test in
  `tests/test_golden_v2.py`, strengthened to catch dict literals as well as keyword arguments.
- **R13 — `evals.run` on a v2 golden:** only the loader's "row N is not human-labeled" `ValueError`
  maps to `config_error`; any other `ValueError` keeps the existing `invalid_golden` code.
- **R14 — the label tool imports no DB layer** (it reads files); the `Candidate` dataclass moves to
  a DB-free `evals/candidates.py` imported by both modules; the sampler's DB URL comes from
  `--database-url` or `Settings().database_url`, never `os.environ` directly.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| sampler strata | `test_sample.py::test_sample_stratifies_by_cheap_category_and_hides_verdicts` (DB fixtures; seed 30 alerts across 5 categories via `seed_alert(..., verdict=…)`) | every present category stratum has ≥ min(5, members) rows; the written JSONL has no `label`, no `severity`, no `category`, no `reasoning` key anywhere (recursive key scan) |
| injection stratum | `::test_injection_candidates_are_oversampled_and_marked` | 3 alerts whose `username`/`input` match `INJECTION_HINT` → all 3 sampled with `stratum == "injection-candidate"` |
| determinism | `::test_same_seed_same_sample` | two runs, same seed → identical `case_id` lists; different seed → different order/selection |
| exclude | `::test_exclude_already_labeled_case_ids` | case ids listed in `--exclude` never appear |
| newest window | `::test_since_filters_received_at` | `--since` excludes older rows |
| CLI | `::test_main_writes_file_and_exits_zero`, `::test_main_exit_1_without_database_url` | exit codes; no URL printed |
| loader v2 | `test_golden_v2.py::test_require_human_rejects_unlabeled_rows` | a row without `labeled_by` → `ValueError` naming the row; `require_human=False` still loads v1 |
| v1 unchanged | `::test_v1_loads_unchanged` | `load_golden(v1)` count 20, `labeled_by is None` |
| label prompt | `test_label_tool.py::test_prompt_label_records_typed_input_only` (fake console feeding `4`, `4`, note, `injection`) | the returned `GoldenCase` equals the typed values; `labeled_by == "human"`; `escalate is True` (auto, severity 4); a note without "6.6" re-prompts |
| resumable | `::test_label_skips_cases_already_in_out_file` | second run labels only the remaining case |
| skip/quit | `::test_skip_and_quit` | `s` skips, `q` stops with the file intact |
| rereview | `::test_rereview_samples_fraction_and_reports_disagreement` | 40 rows, fraction 0.10, seed → 4 cases; fake console disagrees on 1 → `0.25` returned; the printed line says "> 10 %" |
| stats | `::test_stats_table_counts` | strata counts, injection count, `labeled_by` breakdown |
| ONLY writer | `::test_only_label_tool_writes_labeled_by_human` | AST/grep over `evals/**/*.py` and `scripts/**/*.py`: the string `"human"` assigned to `labeled_by` appears ONLY in `evals/label_tool.py::prompt_label`; `evals/sample.py` never imports `GoldenLabel` |
| never logs attacker text | `::test_label_tool_never_logs_case_content` (caplog) | with the fake console, no log record contains any command/username from the candidate |
| taxonomy | `test_taxonomy_agreement.py::test_guide_skill_and_prompt_share_one_taxonomy` | the three sources' category name sets are equal; the tie-break phrase present in guide + skill |
| run.py | `tests/test_evals_run.py` (extend, unpinned for this task) `::test_run_requires_human_labels_for_v2_files` | a `v2*.jsonl` with one unlabeled row → exit 1 `config_error` |

## Steps (TDD)

- [ ] **Step 1 (RED — test-author):** the four new test files + the `test_evals_run.py` extension.
- [ ] **Step 2 (RED — test-author):** `uv run pytest -q tests/test_sample.py tests/test_label_tool.py tests/test_golden_v2.py tests/test_taxonomy_agreement.py tests/test_evals_run.py` (export line) → Expected: import errors on the missing modules; the loader tests fail on the unknown kwarg; the taxonomy test fails on the missing guide. Pin, commit `test(evals): v2 sampler, label tool, human-label contract, taxonomy RED (m7 task-01)`.
- [ ] **Step 3 (GREEN — implementer): loader + sampler** (`uv run mypy --no-incremental` after each).
- [ ] **Step 4 (GREEN — implementer): label tool + stats + rereview.**
- [ ] **Step 5 (implementer): guide, skill sentence, README paragraph, `.gitignore`, `run.py` require_human.** Run the sampler against the DEV stack seeded with `scripts/seed_dev.py` and paste the strata table; run `label` with three typed rows into a SCRATCH out file (never `evals/golden/v2.jsonl` — that file is the author's) and paste `stats`.
- [ ] **Step 6 (implementer): full gates (cold) → commit** `feat(evals): v2 stratified sampler, human-only label tool with re-review, labeling guide (m7 task-01)`.
- [ ] **Step 7 — OWNER (calendar time, not agent time):** run the sampler against production from the box (`docker compose run --rm api uv run python -m evals.sample --n 240 …` with the candidates written to a bind-mounted host path, copied back via SSM), label ≥ 200 rows with `label` on the workstation, commit `evals/golden/v2.jsonl` yourself (`data(golden): v2 labels — <n> rows`), run `rereview` a week later. The controller never touches `v2.jsonl`'s content; it only verifies `stats` and `require_human` at the gate.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_sample.py tests/test_label_tool.py tests/test_golden_v2.py tests/test_taxonomy_agreement.py tests/test_evals_run.py tests/test_golden.py   # all pass, 0 skipped
uv run pytest -q tests/test_golden_v2.py::test_labeled_by_human_assignment_appears_only_in_prompt_label  # the AST guard: only the label tool mints labeled_by="human" (R18/N3 — the earlier grep -c line was unsatisfiable and is removed)
test ! -e evals/golden/v2.jsonl && echo "v2 not created by agents"                   # ok
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- A stratified, verdict-blind candidate file can be produced from the live DB deterministically; the label tool records only typed input, marks every row `labeled_by: human`, resumes, re-reviews a seeded 10 % and reports the disagreement rate; the loader refuses a v2 file with any non-human row; nothing under `evals/` can mint a human label.
- One taxonomy (guide, skill, prompt) with the tie-break, pinned by a test, exists before the author labels.
