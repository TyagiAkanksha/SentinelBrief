---
id: task-05
milestone: m7-eval-hardening
depends_on: [task-03, task-04]
status: planned
spec: PRD.md §7.4 ("Nightly GitHub Actions job runs the harness on the current golden set. Baselines are established at M7 (first full run on v2); thereafter the build fails if: severity exact-match drops >3 points below baseline, or critical recall < 0.90, or mean cost/alert rises >50 % without a config change. Do not invent thresholds before a baseline exists."), §7.2 (`eval_runs` row per run); `docs/plans/m7-eval-hardening.md` Global Constraints (baselines recorded from the first full v2 run IN THIS MILESTONE and committed; gate thresholds are exactly §7.4 — no others); `.claude/rules/evals.md`; `CONVENTIONS.md` §7 (no hardcoded threshold — the three §7.4 numbers are Settings with the PRD values as defaults)
---

# task-05 — `evals/baseline.json` (recorded, never invented), `evals.run --gate` (the three PRD §7.4 conditions, nothing else), and `.github/workflows/nightly-eval.yml`

## Goal

The gate is a pure comparison between a run's `RunMetrics` and a committed baseline: `evals/gate.py::evaluate_gate(metrics, baseline, *, settings) -> GateResult` returns the list of tripped conditions — `severity_exact` more than `EVAL_GATE_SEVERITY_DROP_POINTS` (3) below the baseline's, `critical_recall` below `EVAL_GATE_CRITICAL_RECALL_MIN` (0.90), `cost_mean_usd` above baseline × (1 + `EVAL_GATE_COST_RISE_FRACTION` (0.50)) while the run's `model_config` equals the baseline's (a config change disables the cost condition by design, PRD §7.4) — and `evals.run --gate` exits 1 naming them. `evals/baseline.json` is written ONLY by `evals.run --write-baseline` from a real v2 run (the first full v2 run in this milestone; the controller runs it once and commits it; the file carries the run's date, git sha, prompt version, model_config and the full `RunMetrics`). The nightly workflow runs `evals.run --golden evals/golden/v2.jsonl --prompt <active> --gate` at 03:23 UTC with the LLM key from a repository secret, replays fixtures (strict), uploads the per-run JSON as an artifact, and fails on the gate. Nothing else can trip it.

## Context (read ONLY these)

- `PRD.md` §7.2, §7.4. Spine Global Constraints. `.claude/rules/evals.md`. `CONVENTIONS.md` §7.
- Code you build on: `evals/scoring.py::RunMetrics` (task-04's extended fields), `evals/run.py` (`main`, the per-run JSON payload, `_metrics_payload`), `core/config.py::Settings` (add the three gate Settings + `.env.example` lines, `tests/test_env_example_roster.py`), `.github/workflows/ci.yml` (the python job's setup — copy the uv/python setup steps; the nightly job needs NO postgres/redis services: evals are DB-less; it needs the `LLM_API_KEY` secret, `CHEAP_MODEL`/`STRONG_MODEL`/`MODEL_PRICES_JSON` as workflow `env` — non-secret, pinned in the workflow file to the prod compose's values), task-02's strict replay (the nightly run must fail on a missing fixture too — that is the strict default for `v2*`).

## Files

- Create: `evals/gate.py`, `.github/workflows/nightly-eval.yml`, `evals/baseline.json` (by the controller's `--write-baseline` run, committed as `data(evals): M7 baseline from the first full v2 run`)
- Create (test-author): `tests/test_gate.py`, `tests/test_nightly_workflow_pins.py`
- Modify: `evals/run.py` (`--gate`, `--baseline` (default `evals/baseline.json`), `--write-baseline`), `core/config.py` + `.env.example` (`EVAL_GATE_SEVERITY_DROP_POINTS=3`, `EVAL_GATE_CRITICAL_RECALL_MIN=0.90`, `EVAL_GATE_COST_RISE_FRACTION=0.50`), `docs/results.md` (a "Gate" paragraph), `README.md` (the nightly badge + one sentence), `.claude/rules/evals.md` (the baseline-write rule)

## Interfaces

```python
# evals/gate.py
@dataclass(frozen=True)
class Baseline: recorded_at: datetime; git_sha: str; prompt_version: str; model_config: dict[str, Any]; metrics: RunMetrics
def load_baseline(path: Path) -> Baseline            # ValueError on a missing/invalid file (the gate cannot run without a baseline — it never invents one)
def write_baseline(path: Path, *, metrics, git_sha, prompt_version, model_config, now) -> None
@dataclass(frozen=True)
class GateResult: tripped: tuple[str, ...]; details: dict[str, str]     # tripped ∈ {"severity_exact_drop", "critical_recall", "cost_rise"}; details carries "baseline=… run=… limit=…" per condition
def evaluate_gate(metrics: RunMetrics, baseline: Baseline, *, run_model_config: dict[str, Any], settings: Settings) -> GateResult
    # severity: metrics.severity_exact < baseline.metrics.severity_exact - settings.eval_gate_severity_drop_points / 100  (severity_exact is a 0..1 fraction; "3 points" = 0.03)
    # critical: metrics.critical_recall < settings.eval_gate_critical_recall_min
    # cost: run_model_config == baseline.model_config and metrics.cost_mean_usd > baseline.metrics.cost_mean_usd * (1 + settings.eval_gate_cost_rise_fraction)
# evals/run.py: --gate → after the table, evaluate for EACH --prompt row against the baseline (the baseline's prompt_version need not match — the gate compares the ACTIVE prompt's row; a prompt change is a config change only for the cost condition); print "GATE: PASS" or "GATE: FAIL <cond>=<details>"; exit 1 on any trip. --write-baseline → refuses if the file exists (use --force-baseline to overwrite, printing the old vs new metrics), refuses for a non-v2 golden file.
```

`.github/workflows/nightly-eval.yml`: `on: schedule: - cron: "23 3 * * *"` + `workflow_dispatch`; one job `nightly-eval`; checkout; `astral-sh/setup-uv` pinned as in ci.yml; env `LLM_API_KEY: ${{ secrets.LLM_API_KEY }}`, `CHEAP_MODEL`, `STRONG_MODEL`, `MODEL_PRICES_JSON`, `TRIAGE_PROMPT_VERSION` pinned to the prod compose's values, `LLM_BASE_URL`; step `uv run python -m evals.run --golden evals/golden/v2.jsonl --prompt "$TRIAGE_PROMPT_VERSION" --strong-model "$STRONG_MODEL" --gate --output-dir evals/results`; `actions/upload-artifact` of `evals/results/*.json` (always); no `continue-on-error`. The owner adds the `LLM_API_KEY` repository secret (owner step; the controller can do it with `gh secret set LLM_API_KEY` reading the local `.env` value into the command's stdin — never printed — under the standing grant).

## Controller rulings carried into this task (2026-09-17 — binding, exact)

- **R35 (task-04):** the JSON view of `RunMetrics` is `evals/publish.py::metrics_payload` (public); `evals/run.py::_metrics_payload` no longer exists — every reference above to `_metrics_payload` means `metrics_payload`.
- **R43 — `metrics_from_payload` lands HERE, not in task-06:** `load_baseline` must rebuild a `RunMetrics` from JSON, so this task adds `evals/publish.py::metrics_from_payload(payload: dict[str, Any]) -> RunMetrics` — the exact inverse of `metrics_payload` (string band keys back to `int`, `PR` dicts back to `PR`, confusion lists back to tuples of tuples, category dicts as-is, cost/latency strings back to `Decimal`/`int`, `None` judge fields preserved) with the round-trip test `tests/test_gate.py::test_metrics_from_payload_round_trips` (`metrics_from_payload(metrics_payload(m)) == m` for a judged `score()` result with failed and injection cases). Task-06 reuses it for `--from-artifact` (its brief's R41 is updated to reuse). `Baseline` is serialized as `{"recorded_at": iso, "git_sha", "prompt_version", "model_config", "metrics": metrics_payload(...)}`.
- **`model_config` shape** (the cost condition compares it for equality): the dict task-04's `evals.run --database-url` already writes — `{"model", "judge_model", "replay_strict", "judge"}` — plus `"strong_model"` (the `--strong-model` value or `""`) and `"prompt_version"`; `evals.run` builds it in ONE helper `run_model_config(args, settings, ...) -> dict[str, Any]` used by the `eval_runs` row, the per-run JSON payload (`"model_config"` key added), `--write-baseline` and `--gate`, so the four cannot drift.
- **Nightly workflow env, pinned to `infra/deploy/prod/docker-compose.yml`'s `x-shared-env`:** `LLM_BASE_URL`, `CHEAP_MODEL`, `STRONG_MODEL`, `MODEL_PRICES_JSON`, `TRIAGE_PROMPT_VERSION` — the pin test reads both files and asserts equality for all five (not only `TRIAGE_PROMPT_VERSION`). `LLM_API_KEY` from `secrets.LLM_API_KEY`. The run step is `uv run python -m evals.run --golden evals/golden/v2.jsonl --prompt "$TRIAGE_PROMPT_VERSION" --strong-model "$STRONG_MODEL" --gate --output-dir evals/results` (all five flags exist today). `actions/checkout@v7` and `astral-sh/setup-uv@v10.0.1` with `python-version: "3.12"` exactly as `.github/workflows/ci.yml`; `UV_LOCKED: "1"`.
- **Step 6 (controller, live) is BLOCKED on the owner's v2 labels** (`evals/golden/v2.jsonl` does not exist yet; never fabricated). The implementer therefore ships `evals/baseline.json` ABSENT; `evals.run --gate` with a missing baseline exits 1 with `config_error: no baseline at <path> — write one from a real v2 run with --write-baseline` (pinned by `test_gate_flag_without_baseline_exits_1_config_error`), and the nightly workflow is committed but not dispatched. The controller sets the `LLM_API_KEY` repository secret at the task's close (owner grant in the Files section) and records in the ledger that the first nightly run awaits v2.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| load/write | `test_gate.py::test_write_then_load_roundtrip`, `::test_load_missing_baseline_raises` | round trip equality; missing → `ValueError` (never a default) |
| severity | `::test_severity_drop_trips_at_more_than_three_points` (rule 7: `Settings(eval_gate_severity_drop_points=2)` → trips at 2.5) | exactly 3.0 points below does NOT trip; 3.1 does |
| critical | `::test_critical_recall_below_min_trips` | 0.89 trips; 0.90 does not |
| cost | `::test_cost_rise_trips_only_without_config_change` | +51 % same config → trips; +51 % with a changed `model_config` → does not; +49 % → does not |
| result | `::test_gate_result_lists_every_tripped_condition` | all three at once → three names, deterministic order |
| defaults | `tests/test_config.py::test_eval_gate_defaults` (R17 literals) | 3, 0.90, 0.50 |
| run --gate | `tests/test_evals_run.py` (extend) `::test_gate_flag_exits_1_and_prints_conditions`, `::test_write_baseline_refuses_existing_and_non_v2` | FakeLLMClient runs against a tmp baseline |
| workflow | `test_nightly_workflow_pins.py::test_nightly_workflow_shape` | YAML parses (`python -c "import yaml"` if available, else text pins): cron `23 3 * * *`, `workflow_dispatch`, the `--gate` step, `secrets.LLM_API_KEY`, upload-artifact `if: always()`, NO `continue-on-error`, no postgres/redis service, `TRIAGE_PROMPT_VERSION` equals `infra/deploy/prod/docker-compose.yml`'s `x-shared-env` value (read both files) |

## Steps (TDD)

- [ ] Steps 1–2 (test-author): RED; commit `test(evals): gate conditions, baseline contract, nightly workflow pins RED (m7 task-05)`.
- [ ] Steps 3–5 (implementer): gate + Settings + run flags + workflow + docs; full gates; commit `feat(evals): PRD §7.4 gate against a recorded baseline; nightly workflow (m7 task-05)`.
- [ ] **Step 6 — controller (live, once):** `uv run python -m evals.run --golden evals/golden/v2.jsonl --prompt triage-v4 --strong-model gpt-5.4 --write-baseline` with the local `.env` → `evals/baseline.json`; commit `data(evals): M7 baseline …`; `gh secret set LLM_API_KEY < <(…)` (never printed); `gh workflow run nightly-eval.yml` → the run link in the ledger (PASS expected on the same day).

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_gate.py tests/test_nightly_workflow_pins.py tests/test_evals_run.py tests/test_config.py tests/test_env_example_roster.py   # all pass, 0 skipped
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov=sentinelbrief_shipper --cov-fail-under=90
```

## Acceptance

- The gate is exactly PRD §7.4, parameterized by three Settings with the PRD defaults, evaluated against a baseline that only a real v2 run can write; the nightly workflow runs the strict-replay harness and fails on the gate and on nothing else.
