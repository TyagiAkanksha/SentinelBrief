# m0-core-loop — Core loop (CLI) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Implement with superpowers:test-driven-development;
> claim completion only via superpowers:verification-before-completion.

**Spec:** `PRD.md` §12 M0 — **authoritative for every behavior in this milestone.** Primary
sections: §1.2 (alert unit and summary), §4 (stack, layout), §6.5 (structured output contract),
§6.6 (severity rubric), §10.6 (prompt-injection posture), §13 (open items — model ids are the
owner's). Briefs repeat what an implementer needs; on any conflict the PRD wins.
**Conventions:** `CONVENTIONS.md` (all sections) · `CLAUDE.md` (working rules).

**Goal:** the §12 M0 definition of done — `uv run python -m worker.triage_one
fixtures/alerts/alert1.json` prints a validated `Verdict`; it runs on all five fixtures; a
structured-output validation failure is retried once and then errors cleanly — plus the tooling
gates (ruff / ruff format / mypy strict / import-linter / pytest) and CI that every later
milestone's Global Constraints assume. No web, no DB, no tools.

**Architecture:** one `uv` project at the repo root with packages `api/ worker/ core/ evals/`
(PRD §4). `core/` holds config, typed errors, the `Verdict` and `SessionAlert` schemas and the
`LLMClient` Protocol; `worker/` holds the only SDK-backed LLM client, the prompt loader, the
session summarizer, the `TriagePipeline` (prompt → LLM → validate → retry once) and the CLI.
Import-linter contracts (CONVENTIONS.md §2) are live from task-01 so `api/` can never grow an LLM
import later. The LLM seam is a Protocol with one fake (`tests/fakes.py::FakeLLMClient`), so every
test in M0–M2 runs without a network.

**Tech Stack:** Python 3.12 · uv · Pydantic v2 + pydantic-settings · `openai` SDK (any
OpenAI-compatible endpoint via `LLM_BASE_URL`) · httpx · pytest + pytest-asyncio + pytest-cov ·
ruff · mypy strict · import-linter · GitHub Actions.

## Global Constraints

Every task's requirements implicitly include this section.

- Work on branch `feat/m0-core-loop` off `main`; **path-scoped `git add` only** — never
  `git add .` / `-A`; never stage `.env` or secrets. Conventional Commits with scope + task id
  (`feat(core): settings and verdict schema (m0 task-02)`), both trailers from CONVENTIONS.md §12.
- **Python gates before every commit** (repo root): `uv run ruff check .` · `uv run ruff format
  --check .` · `uv run mypy` · `uv run lint-imports` · `uv run pytest -q` — all clean. No DB in
  M0, so no export line is needed yet; from task-01 on, `uv run pytest -q` must report **0
  skipped**.
- `uv run mypy` after every implementation or significant change, not only at the gate.
- **No LLM outside `worker/`**: `core/llm.py` is an interface (no SDK import);
  `worker/llm_client.py` is the only module importing `openai`. Contract 3 fails CI otherwise.
- **Never hardcode a model id, price, or threshold.** Tests use `FakeLLMClient` and a
  `Settings(cheap_model="fake-model", model_prices_json={"fake-model": {...}})` fixture.
- **Prompt files are immutable once shipped**; `triage-v1.md` gets its sha256 pin in task-04.
- **Attacker data is delimited** (PRD §10.6): the summary goes between `<<<ALERT_DATA>>>` and
  `<<<END_ALERT_DATA>>>`; the prompt carries the "data, never instructions" sentence.
- PRD ambiguities that affect schema or behavior are RAISED to the owner, not guessed;
  implementation details are decided and recorded in the report. Enhancements →
  `SUGGESTIONS.md`.
- Live-API tests are `@pytest.mark.live`, excluded by `addopts`; the acceptance walk runs them once
  with the owner's key exported and pastes the output (never the key) into the ledger.

## Tasks

| # | Task | File | Depends on |
|---|------|------|-----------|
| 1 | Repo scaffold, uv project, tooling gates, gates-as-tests, CI, lefthook, dependabot | `m0-core-loop/task-01-repo-scaffold-gates-ci.md` | — |
| 2 | `Settings`, typed errors, `Verdict` + `SessionAlert` schemas, five fixtures | `m0-core-loop/task-02-config-errors-schemas-fixtures.md` | task-01 |
| 3 | `LLMClient` Protocol, OpenAI-compatible async client, `FakeLLMClient` | `m0-core-loop/task-03-llm-protocol-client-fake.md` | task-02 |
| 4 | Prompt `triage-v1`, session summary, `TriagePipeline` with one retry | `m0-core-loop/task-04-prompt-summary-pipeline.md` | task-03 |
| 5 | `worker.triage_one` CLI, live smoke, M0 acceptance walk + tag | `m0-core-loop/task-05-triage-one-cli-acceptance.md` | task-04 |

Order: 1 → 2 → 3 → 4 → 5, strictly sequential. Rationale: task-01 pins the package names, tool
config and gate commands every later Steps block assumes; task-02 pins `Settings` / `Verdict` /
`SessionAlert` / error names that the client and pipeline import; task-03 pins the `LLMClient`
seam and the fake that every later test (M1 evals, M2 inline triage) injects; task-04 is the
actual loop; task-05 is the acceptance surface.

## Acceptance walk (PRD §12 M0)

| Clause | Demonstrated by |
|---|---|
| `python -m worker.triage_one fixtures/alerts/alert1.json` prints a validated `Verdict` | task-05 `test_prints_verdict_json_exit_zero`; live run pasted into the ledger |
| Runs on 5 fixture alerts | task-02 fixtures; task-05 `test_runs_all_five_fixtures` + `test_live_triage_all_fixtures` |
| Schema validation failures retry once then error cleanly | task-04 `test_run_retries_once_appending_validation_error`, `test_run_raises_verdict_validation_error_after_second_failure`; task-05 `test_exit_2_on_verdict_validation_error_no_traceback` |
| (added v1.1) tooling gates + CI green | task-01 gates-as-tests; first CI run on the PR |

## Status

planned — snapshot only; git history and the ledger are authoritative.
