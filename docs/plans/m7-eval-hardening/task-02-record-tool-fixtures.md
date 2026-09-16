---
id: task-02
milestone: m7-eval-hardening
depends_on: [task-01]
status: planned
spec: PRD.md §7.2 ("Runs the full pipeline (tools included, against recorded tool-result fixtures for determinism)"), §6.3 (the five tools; two are external), §7.4 (a nightly gate needs determinism); `docs/plans/m7-eval-hardening.md` Global Constraints ("Recorded tool fixtures are committed; a v2 case whose fixture is missing fails the eval loudly rather than going live"); `.claude/rules/evals.md` ("Tool calls replay recorded fixtures … the LLM is the only live component of an eval run"); `CONVENTIONS.md` §10
---

# task-02 — `evals/record.py` (record every external tool result the v2 cases can request, once, into `tests/fixtures/tools/`), strict replay in `evals.run` (a missing fixture fails the run, never silently `unavailable`), and the determinism proof

## Goal

A published number must be reproducible: the same golden file, prompt and model must give the same
tool inputs on every run, so the only nondeterminism left is the LLM's. Today `evals.run` replays
`tests/fixtures/tools/` through `ReplayToolRecorder`, but a missing fixture degrades to
`{"unavailable": true, "reason": "fixture missing"}` — the eval still runs, with different tool
evidence than production saw, and nobody notices. This task adds `evals/record.py`, which walks
every v2 case and calls each EXTERNAL tool live ONCE for every argument set the model can
legitimately request for that case (`get_ip_geo_asn` and `lookup_ip_reputation` on the session's
`src_ip`), writing fixtures with the existing `write_fixture` layout, and it gives `evals.run` a
`--replay-strict` mode (default ON for `v2*` golden files) under which `ReplayToolRecorder` raises
a `FixtureMissingError` that the run turns into a per-case `error` AND a non-zero exit with the list
of missing `(tool, key)` pairs, so a new v2 row without its fixtures fails CI loudly. The proof: two
replayed runs over v2 with a `FakeLLMClient` are byte-identical in their per-case tool calls.

## Context (read ONLY these)

- `PRD.md` §6.3, §7.2, §7.4. Spine Global Constraints. `.claude/rules/evals.md`.
- Code you build on: `worker/tools/recorder.py` (`fixture_key`, `fixture_path`, `write_fixture`,
  `LiveToolRecorder(record_dir=…)`, `ReplayToolRecorder(fixtures_dir)` — a missing fixture is
  `unavailable(...)` today), `worker/tools/base.py` (`Tool.external`, `ToolContext`),
  `worker/tools/geo.py` + `worker/tools/reputation.py` (the two external tools; their argument
  schema — `{"ip": …}`), `evals/run.py` (`--tool-fixtures`, `_run_all`, `run_golden`),
  `tests/fixtures/tools/README.md`, `tests/test_evals_replay.py`, `tests/test_tool_recorder.py`,
  `evals/golden/__init__.py` (task-01's `GoldenCase`), `tests/fakes.py::FakeLLMClient`.

## Files

- Create: `evals/record.py`; fixtures under `tests/fixtures/tools/<tool>/<key>.json` (recorded by
  the OWNER's run against the real APIs — Step 7; the agent records nothing live in CI)
- Create (test-author): `tests/test_record.py`, `tests/test_replay_strict.py`, `tests/test_eval_determinism.py`
- Modify: `worker/tools/recorder.py` (`ReplayToolRecorder(fixtures_dir, *, strict: bool = False)`;
  `class FixtureMissingError(ConfigError)` in `core/errors.py` with `code = "fixture_missing"` (ruling R24: subclassing `ConfigError` lets `api/errors.py`'s MRO walk resolve it to 500 without any file under `api/` naming it — the M2 invariant "every concrete error has a status" and the task-02 pin "never referenced under api/" both hold)
  — raised only when `strict`), `evals/run.py` (`--replay-strict/--no-replay-strict`, default
  strict when the golden basename starts with `v2`; missing-fixture cases become
  `CaseResult.error = "fixture_missing:<tool>:<key>"` and the run exits 1 after printing the
  table plus the missing list), `tests/fixtures/tools/README.md` (the recording procedure),
  `CONVENTIONS.md` §4 (the new error), `.claude/rules/evals.md` (one sentence: strict replay for v2)

## Interfaces

```python
# evals/record.py — python -m evals.record --golden evals/golden/v2.jsonl [--fixtures tests/fixtures/tools] [--only get_ip_geo_asn] [--dry-run]
def planned_calls(cases: Sequence[GoldenCase]) -> list[tuple[str, dict[str, Any]]]     # for each case: ("get_ip_geo_asn", {"ip": alert.src_ip}), ("lookup_ip_reputation", {"ip": alert.src_ip}); de-duplicated, sorted — the exact argument sets the pipeline can produce for a case (the tools take the session's src_ip only)
async def record(cases, *, registry: ToolRegistry, fixtures_dir: Path, only: str | None, dry_run: bool) -> RecordReport   # skips existing fixtures (idempotent); calls `registry.execute` under a LiveToolRecorder(record_dir=fixtures_dir); RecordReport(planned, recorded, skipped_existing, failed: list[(tool, key, reason class)])
def main(argv: Sequence[str] | None = None, *, registry: ToolRegistry | None = None) -> int   # exit 0; 1 on any failure (names/keys only); --dry-run prints the plan and exits 0
# worker/tools/recorder.py
class ReplayToolRecorder:
    def __init__(self, fixtures_dir: Path, *, strict: bool = False) -> None
    # strict and the fixture is missing → raise FixtureMissingError(f"{tool}:{key}") (the registry's execute boundary must let it propagate — it is not a tool failure; add the one `except FixtureMissingError: raise` above the registry's catch-all, documented in CONVENTIONS §4's carve-out sentence)
# evals/run.py: --replay-strict default = golden.name.startswith("v2"); per-case FixtureMissingError → CaseResult(error="fixture_missing:<tool>:<key>", verdict=None); after the table: "MISSING FIXTURES (n): tool key …" and return 1
```

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| plan | `test_record.py::test_planned_calls_one_per_external_tool_per_src_ip_deduplicated` | 3 cases, 2 sharing an ip → 4 planned calls, sorted |
| record | `::test_record_writes_fixtures_via_live_recorder_and_skips_existing` (a registry with fake external tools returning canned dicts; `tmp_path` fixtures) | files at `fixture_path(...)`; second run `skipped_existing == 4`, `recorded == 0` |
| dry-run + failure | `::test_dry_run_writes_nothing`, `::test_failed_tool_reported_by_class_only` | no files; a raising tool → `failed == [(tool, key, "RuntimeError")]`, exit 1, no message text |
| strict replay | `test_replay_strict.py::test_strict_missing_fixture_raises_fixture_missing_error` | `ReplayToolRecorder(dir, strict=True).execute(...)` raises; non-strict returns `unavailable` |
| registry passthrough | `::test_registry_does_not_swallow_fixture_missing_error` | mutant: remove the re-raise → the error is swallowed into `unavailable` → test fails |
| run strict | `::test_run_v2_defaults_strict_and_exits_1_listing_missing` (FakeLLMClient; a `v2-tiny.jsonl` in tmp with two cases, fixtures for one) | exit 1; stdout lists `fixture_missing:lookup_ip_reputation:<key>`; the other case scored |
| run non-strict | `::test_no_replay_strict_flag_keeps_v1_behaviour` | `--no-replay-strict` → exit 0, `unavailable` result |
| determinism | `test_eval_determinism.py::test_two_replayed_runs_produce_identical_tool_call_sequences` | run twice with the same FakeLLMClient responses → the recorded per-case `tool_calls` and results JSON are byte-identical |

## Steps (TDD)

- [ ] Steps 1–2 (test-author): the three files; RED evidence; pin; commit `test(evals): record + strict replay + determinism RED (m7 task-02)`.
- [ ] Steps 3–5 (implementer): `FixtureMissingError` + strict recorder + registry passthrough; `evals/record.py`; `evals.run` flag + exit; docs. Full gates. Commit `feat(evals): record external tool fixtures for v2; strict replay fails on a missing fixture (m7 task-02)`.
- [ ] **Step 6 — OWNER/controller (live APIs, once):** after `v2.jsonl` exists, `uv run python -m evals.record --golden evals/golden/v2.jsonl` on the workstation with the real `.env` keys (MaxMind: the local `infra/geoip/*.mmdb`; AbuseIPDB: one check per distinct src_ip — count first with `--dry-run`, ~200 calls fits the free daily quota); commit the fixtures `data(fixtures): tool fixtures for golden v2`. Then `uv run python -m evals.run --golden evals/golden/v2.jsonl --prompt triage-v4` must exit 0 with zero missing.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_record.py tests/test_replay_strict.py tests/test_eval_determinism.py tests/test_evals_replay.py tests/test_tool_recorder.py tests/test_evals_run.py   # all pass, 0 skipped
uv run python -m evals.record --golden evals/golden/v1.jsonl --dry-run | head -3      # prints the plan (v1: 20 ips)
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- Every external tool result a v2 case can request is recorded once and committed; a v2 eval run with any missing fixture exits non-zero naming it; two replayed runs are byte-identical in their tool evidence.
