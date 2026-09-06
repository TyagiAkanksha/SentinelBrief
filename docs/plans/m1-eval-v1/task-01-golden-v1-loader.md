---
id: task-01
milestone: m1-eval-v1
depends_on: []
status: planned
spec: PRD.md §7.1 (v1 policy), §6.6 (labels follow the rubric), §10.6 (injection cases), §13 (v1 labels may be machine-authored); CONVENTIONS.md §10
---

# task-01 — Golden set v1 (20 labeled synthetic sessions) and the `GoldenCase` loader

## Goal

`evals/golden/v1.jsonl` holds 20 synthetic Cowrie sessions labeled per PRD §6.6 — at least three
per severity band, every category used at least once, at least two injection cases (one via
`username`, one via `cowrie.command.input`) — and `evals/golden.py::load_golden` validates and
loads them into `GoldenCase` objects with unique ids. v1 is a development set: its numbers are
never published.

## Context (read ONLY these)

- `PRD.md` §7.1, §6.6, §10.6, §13.
- `.claude/rules/evals.md` · `.claude/skills/cowrie-fixture/SKILL.md` (use `/cowrie-fixture` for
  every row).
- `core/schemas/alert.py`, `core/schemas/verdict.py` (M0 task-02).

## Files

- Create: `evals/golden.py`, `evals/golden/v1.jsonl`, `evals/golden/README.md`,
  `tests/test_golden.py`
- Delete: `evals/golden/.gitkeep`

## Interfaces

- **Consumes:** `SessionAlert`, `VerdictCategory`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # evals/golden.py
  class GoldenLabel(BaseModel):
      severity: Annotated[int, Field(ge=1, le=5)]
      category: VerdictCategory
      escalate: bool
      # model_validator: severity >= 4 requires escalate True (PRD §6.6)
  class GoldenCase(BaseModel):
      alert: SessionAlert
      label: GoldenLabel
      labeler_note: str                       # min_length=10; cites the §6.6 row
      tags: list[str] = []                    # "injection" marks injection cases
      @property
      def case_id(self) -> str: ...           # alert.fingerprint()
  def load_golden(path: Path) -> list[GoldenCase]: ...
      # one JSON object per non-empty line; ValueError("row N: ...") on a bad row; ValueError on duplicate case_id
  ```

  `v1.jsonl` row shape: `{"alert": {...SessionAlert...}, "label": {"severity": 4, "category":
  "successful_intrusion", "escalate": true}, "labeler_note": "§6.6 sev 4: ...", "tags": []}`.
  `README.md` states: v1 rows and labels are machine-authored synthetic data (allowed by PRD
  §13); v1 numbers are never published; v2 (M7) is real traffic hand-labeled by the author; the
  command-based injection row only exercises the pipeline from M4 on, the username-based one
  from M0.

## Steps (TDD)

- [ ] **Step 1: Write failing tests** in `tests/test_golden.py` (loading `evals/golden/v1.jsonl`
  through `load_golden`): `test_v1_loads_20_cases`, `test_case_ids_unique`,
  `test_every_severity_band_present` (≥3 per band 1–5), `test_every_category_used`,
  `test_at_least_two_injection_cases_one_via_username` (≥2 rows tagged `injection`; at least one
  has an event with `username` containing `"ignore"` and at least one has a `command.input`
  event whose `input` contains `"ignore"`), `test_labels_respect_escalate_rule`,
  `test_rejects_invalid_row` (tmp file with `severity: 9` → `ValueError` mentioning `row 1`),
  `test_rejects_duplicate_case_id` (same alert twice → `ValueError`).
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: evals.golden`.
- [ ] **Step 3: Implement `evals/golden.py`.**
- [ ] **Step 4: Author the 20 rows with `/cowrie-fixture`**, each a complete, distinct session
  (different IPs, sensors, timestamps, credential lists); labels earn their band by behavior;
  `labeler_note` cites the rubric row and the evidence. Include the two injection kinds. Write
  `README.md`.
- [ ] **Step 5: Run the tests → pass; `uv run mypy` clean.**
- [ ] **Step 6: Full gates → commit:**
  `feat(evals): golden v1 (20 synthetic labeled sessions) and GoldenCase loader (m1 task-01)`.

## Verify

```bash
uv run pytest -q tests/test_golden.py                                   # 8 passed
wc -l evals/golden/v1.jsonl                                             # 20
uv run python -c "from pathlib import Path; from evals.golden import load_golden; cs=load_golden(Path('evals/golden/v1.jsonl')); print(sorted(c.label.severity for c in cs)); print(sum('injection' in c.tags for c in cs))"
```

## Acceptance

- 20 valid rows; ids unique; ≥3 per band; every category present; ≥2 injection rows of two
  kinds; escalate rule holds on every label.
- `README.md` records the v1 policy (never published; v2 labels are human work).
