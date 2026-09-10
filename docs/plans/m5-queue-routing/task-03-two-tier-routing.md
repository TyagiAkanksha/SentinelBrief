---
id: task-03
milestone: m5-queue-routing
depends_on: [task-02]
status: planned
spec: PRD.md §6.4 (every alert goes to the cheap model first; escalate the SAME context to the strong model iff severity ≥ 4 OR confidence < 0.6; record both models and the escalation decision on the verdict; thresholds live in config), §5 (`model_primary`, `model_final`, `escalated_model`), §6.2 (the retry-budget bound gains the strong tier's calls — v1.4), §7.2/§7.3 (evals report the escalation rate), §12 M5 ("two-tier routing live with config thresholds"), §13 (`STRONG_MODEL` and its price are the owner's); CONVENTIONS.md §7 (never a literal model id or threshold; an unpriced configured model is a boot-time `ConfigError`); `.claude/rules/worker.md` (routing thresholds from `ESCALATE_SEVERITY_GTE` / `ESCALATE_CONFIDENCE_LT`; tokens, cost and latency summed across both tiers)
---

# task-03 — Two-tier routing: `worker/routing.py::should_escalate` (pure), the strong-model second pass over the same conversation (no new tool calls), `model_primary` / `model_final` / `escalated_model` on every verdict, `--strong-model` on the CLIs, `escalation_rate` in the eval table; first-touch carry-overs in the loop test files

## Goal

`TriagePipeline.run` becomes two-tier: after the cheap pass (the M4 loop, unchanged) produces a
validated verdict, the pure decision `should_escalate(verdict, severity_gte=…, confidence_lt=…)`
says whether to escalate; when it does and a `strong_model` is configured, ONE tool-less
`complete_structured` call on the strong model is made over **the same conversation the cheap
model saw** — system prompt, delimited summary, every tool-call turn and delimited tool result,
and the forced-final instruction if the cap was reached — but **without** the cheap model's own
verdict text (no anchoring), with the one PRD §6.5 validation retry. The outcome's verdict is the
strong model's; `model` (= `model_final`) is the strong id, `model_primary` the cheap id,
`escalated_model` `True`; tokens, cost and LLM latency are summed across both tiers and the tool
trace is the cheap pass's (the strong pass adds no tool calls). A strong-tier failure
(`VerdictValidationError` after its retry, or `LLMCallError`) fails the attempt like any other
failure — task-02's job retries it; no silent fallback to the cheap verdict, because
`escalated_model=True` with `model_final=cheap` would misrecord the decision. With
`STRONG_MODEL` empty, routing is off: nothing escalates, `model_primary == model_final`,
`escalated_model=False` — today's behaviour, byte for byte (the mechanical definition is in the
test table). Thresholds and the strong id are `Settings`; `STRONG_MODEL == CHEAP_MODEL` is a
`ConfigError` at boot. `evals.run` grows `--strong-model` and an `escalation_rate` column;
`worker.triage_one` grows `--strong-model` and prints the two routing fields. The PRD §6.2 cost
bound gains the strong tier's two calls (v1.4). The M4 "first touch" carry-overs land here: the
`EchoTool`/`BoomTool`/registry helpers duplicated across the loop test files lift into
`tests/helpers.py` (M4 task-01 N5).

## Context (read ONLY these)

- `PRD.md` §5 (`verdicts` columns), §6.2 (second paragraph — you amend it), §6.4, §6.5, §7.2,
  §7.3, §13, §15.
- `docs/plans/m5-queue-routing.md` — Global Constraints (routing thresholds are settings; the
  decision is a pure function with exhaustive tests; `STRONG_MODEL` ruling).
- `CONVENTIONS.md` §7, §10; `.claude/rules/{worker,evals,tests}.md`.
- Task-02 outputs: `worker/triage.py::triage_attempt` (where `persist_verdict` is now called),
  `AttemptResult`; `worker/jobs.py` (unchanged here).
- Code you build on: `worker/triage.py::run` (the closures `_outcome`, `_retry_once`, `_final`
  — each takes the model as a parameter after this task), `worker/outcome.py::TriageOutcome`,
  `worker/store.py::persist_verdict` (already takes `model_primary`, `escalated_model`),
  `worker/llm_client.py::OpenAICompatibleLLMClient.from_settings` (already refuses an unpriced
  `strong_model`), `evals/scoring.py` (`CaseResult`, `RunMetrics`, `COLUMNS`, `format_table`),
  `evals/run.py`, `worker/triage_one.py`, `scripts/seed_dev.py` (`--live` only),
  `core/schemas/alerts_read.py::VerdictOut` (already exposes the three columns — no wire change),
  `web/src/components/alerts/RoutingInfo` (already renders them — no web change).
- Tests you re-open: `tests/test_tool_loop.py`, `tests/test_tool_loop_db.py`,
  `tests/test_tool_registry.py` (helper lift), `tests/test_triage_pipeline.py`,
  `tests/test_scoring.py`, `tests/test_evals_run.py`, `tests/test_triage_one.py`,
  `tests/test_triage_alert.py`, `tests/test_env_example_roster.py`
  (`ESCALATE_SEVERITY_GTE`/`ESCALATE_CONFIDENCE_LT` leave `_SCHEDULED`).

## Files

- Create: `worker/routing.py`
- Create (test-author): `tests/test_routing.py`
- Modify (test-author, re-pinned): `tests/helpers.py` (+ `EchoTool`, `BoomTool`, `make_registry`,
  `minimal_alert`), `tests/test_tool_loop.py`, `tests/test_tool_loop_db.py`,
  `tests/test_tool_registry.py`, `tests/test_triage_pipeline.py`, `tests/test_scoring.py`,
  `tests/test_evals_run.py`, `tests/test_triage_one.py`, `tests/test_triage_alert.py`,
  `tests/test_env_example_roster.py`
- Modify: `worker/triage.py`, `worker/outcome.py`, `worker/triage_one.py`, `evals/scoring.py`,
  `evals/run.py`, `scripts/seed_dev.py`, `core/config.py`, `.env.example`, `README.md`
  (Evaluation: the `escalation_rate` column and `--strong-model`), `PRD.md` (§6.2 bound, §6.4
  two sentences, §15 v1.4 bullet)

## Interfaces

- **Consumes:** `Verdict`; `TriageOutcome`, `ToolCallRecord`; `persist_verdict`;
  `TriagePipeline` internals from M4 (`_final`, `_retry_once`, `_outcome`, `FINAL_VERDICT_INSTRUCTION`);
  `CaseResult`, `RunMetrics`, `COLUMNS`, `format_table`, `score`; `FakeLLMClient`, `FakeCall`.
- **Produces (task-04/05 and M7/M8 rely on — produce exactly):**

  ```python
  # core/config.py — graduate from _SCHEDULED (+ .env.example comments lose "(from M5)")
  escalate_severity_gte: Annotated[int, Field(ge=1, le=5)] = 4             # ESCALATE_SEVERITY_GTE=4
  escalate_confidence_lt: Annotated[float, Field(ge=0.0, le=1.0)] = 0.6    # ESCALATE_CONFIDENCE_LT=0.6
  # strong_model: str = "" already exists (M0); "" means routing is OFF

  # worker/routing.py — pure; no I/O
  EscalationReason = Literal["severity", "confidence", "none"]
  @dataclass(frozen=True)
  class RoutingDecision:
      escalate: bool
      reason: EscalationReason        # "severity" wins when both conditions hold (deterministic; pinned)
  def should_escalate(verdict: Verdict, *, severity_gte: int, confidence_lt: float) -> RoutingDecision: ...
      # verdict.severity >= severity_gte -> ("severity"); elif verdict.confidence < confidence_lt -> ("confidence"); else (False, "none")
      # worked table at the defaults (4, 0.6): (sev 4, conf 0.9) -> severity; (3, 0.59) -> confidence; (3, 0.6) -> none (strict <); (5, 0.1) -> severity;
      #   (1, 0.6) -> none; (4, 0.1) -> severity; (3, 0.0) -> confidence; (3, 1.0) -> none

  # worker/outcome.py — TriageOutcome gains two defaulted fields (every existing construction keeps working)
  model_primary: str | None = None      # None -> the same as `model` (no routing); persist_verdict receives `model_primary or model`
  escalated_model: bool = False

  # worker/triage.py
  class TriagePipeline:
      def __init__(self, *, llm, model, prompt_version, tools=None, tool_loop_max_iter=None,
                   strong_model: str | None = None, escalate_severity_gte: int | None = None, escalate_confidence_lt: float | None = None) -> None
          # strong_model given -> both thresholds required (ValueError "escalation thresholds are required with strong_model"); strong_model == model -> ValueError
          # strong_model None  -> thresholds ignored (routing off)
      @classmethod
      def from_settings(cls, settings, *, llm, recorder=None, cache=None, http=None) -> TriagePipeline
          # strong = settings.strong_model or None; strong == settings.cheap_model -> ConfigError("STRONG_MODEL must differ from CHEAP_MODEL")
          # passes strong_model=strong, escalate_severity_gte=settings.escalate_severity_gte, escalate_confidence_lt=settings.escalate_confidence_lt
      @property
      def strong_model(self) -> str | None
      async def run(self, alert, *, session=None, now=None) -> TriageOutcome:
          # 1. cheap pass: today's body, with `_outcome(result, *, model, retried)`, `_retry_once(msgs, first_err, *, model)`, `_final(msgs, *, model)`
          #    all parameterised by model (they still share the running totals); returns `cheap: TriageOutcome` (model_primary=None, escalated_model=False)
          # 2. if self._strong_model is None: return cheap
          # 3. decision = should_escalate(cheap.verdict, severity_gte=..., confidence_lt=...)
          #    if not decision.escalate: return cheap
          # 4. logger.info("routing escalated reason=%s cheap_severity=%d cheap_confidence=%.2f cheap=%s strong=%s", ...)   # no attacker strings
          #    strong = await _final(messages, model=self._strong_model)      # `messages` is the cheap conversation as it stands: system + delimited summary
          #                                                                   #   + every tool-call turn/result (+ FINAL_VERDICT_INSTRUCTION on the cap path);
          #                                                                   #   the cheap verdict text is NOT appended; no tools offered; one validation retry
          #    return replace(strong, model=self._strong_model, model_primary=self._model, escalated_model=True,
          #                   retried=cheap.retried or strong.retried, tool_calls=cheap.tool_calls)
          #    (`strong`'s tokens/cost/latency already include the cheap pass because the closures accumulate; the pin in the table checks the sum)
          # Failure policy: a strong-tier StructuredOutputError follows `_retry_once` (one retry) then VerdictValidationError; LLMCallError propagates. Both fail the attempt.
      async def triage_attempt(...):   # persist_verdict(..., model_primary=outcome.model_primary or outcome.model, escalated_model=outcome.escalated_model, tool_calls=outcome.tool_calls)

  # worker/triage_one.py — `--strong-model` (default settings.strong_model; "" disables); output JSON gains "model_primary" and "escalated_model"
  # evals/scoring.py
  CaseResult.escalated: bool = False                                   # defaulted: M1/M4 constructions unchanged
  RunMetrics.escalation_rate: float                                    # escalated / n_cases (failed cases count as not escalated); 0.0 when n_cases == 0
  COLUMNS: "escalation_rate" inserted immediately after "critical_recall"; format_table renders it like the other ratios
  # evals/run.py — `--strong-model` (default settings.strong_model; "" = off); price check "before spend" covers the strong id too:
  #   a non-empty strong model absent from MODEL_PRICES_JSON -> exit 1 `config_error` before any case runs (only when llm is None, like --model);
  #   every TriagePipeline gets strong_model=<value or None>, escalate_severity_gte/lt from settings; CaseResult(escalated=outcome.escalated_model)
  # scripts/seed_dev.py — `--live` passes strong_model=settings.strong_model or None + the thresholds; the fake path passes strong_model=None
  #   (a canned fake has no strong reply scripted: routing must stay off there — pinned by
  #   tests/test_seed_dev.py::test_fake_path_never_routes_even_with_strong_model_set, which exports STRONG_MODEL; the existing
  #   25-row seed tests run with STRONG_MODEL unset and therefore cannot pin it — review PC1, fix-1)
  ```

  **PRD v1.4 (docs step, same commit):** §6.2 bound → "at most `(TOOL_LOOP_MAX_ITER + 4) × 3` LLM
  calls (10 × 3 = 30 at the defaults: six tool turns, the forced final verdict and the one
  validation retry on the cheap tier, plus the strong tier's call and its one validation retry,
  times three attempts)" — **computed (rule 3): 6 + 1 + 1 + 1 + 1 = 10 per attempt; the briefing
  draft's "12 × 3 = 36" was an arithmetic slip, corrected 2026-09-10**; §6.4 gains: "The strong model receives the cheap pass's conversation
  as it stands (system prompt, delimited summary, every tool call and its delimited result) and
  answers with no tools — routing never re-runs the tool loop and never shows the cheap verdict
  to the strong model. A strong-tier failure fails the attempt (§6.2 retries it); there is no
  fallback to the cheap verdict." + one §15 bullet under the v1.4 entry task-02 created.

  **Ownership note (owner input, recorded here so the dispatch does not restate it):**
  `STRONG_MODEL` is `gpt-5.4-2026-03-05` in the local `.env` once the owner adds its
  `MODEL_PRICES_JSON` entry (probed OK at the M4 gate: temperature 0 + `json_object` + tools);
  every unit test uses `strong_model="strong-model"` with a priced fake. The live escalation
  clause of the acceptance walk needs the real value.

## Interfaces → test table

Verdict literals used below (each built through `Verdict` so it always validates; every
`*_STRONG` variant is the same verdict with `reasoning` prefixed `"strong tier: "` so the two
tiers' verdicts are distinguishable in assertions — rule 7): `VALID1` (sev 1, conf 0.9,
`scanning`, escalate false), `VALID2` (sev 2, conf 0.99, `brute_force`, false), `VALID3_LOW`
(sev 3, conf 0.2, `reconnaissance`, false), `VALID4` (sev 4, conf 0.9, `successful_intrusion`,
true — the M2 literal), `VALID5` (sev 5, conf 0.95, `malware_delivery`, true), and
`VALID1_STRONG`, `VALID2_STRONG`, `VALID3_STRONG`, `VALID4_STRONG`. A `FakeCall.model` of
`"strong-model"` is how the strong call is identified; the pipeline's cheap model is
`"fake-model"`.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| decision table | `tests/test_routing.py::test_should_escalate_worked_table` | the eight rows above, parametrized; plus `severity_gte=5`: `(4, 0.9)` → none, `(5, 0.9)` → severity; `confidence_lt=0.0`: `(3, 0.0)` → none |
| severity wins | `tests/test_routing.py::test_severity_reason_takes_precedence_over_confidence` | `(5, 0.1)` → `reason == "severity"` |
| frozen | `tests/test_routing.py::test_routing_decision_is_frozen` | `dataclasses.FrozenInstanceError` |
| constructor bounds | `tests/test_tool_loop.py::test_strong_model_requires_thresholds_and_a_different_id` | `strong_model="strong-model"` without thresholds → `ValueError`; `strong_model="fake-model"` (== `model`) → `ValueError`; thresholds without a strong model → constructs fine |
| off = byte-for-byte M4 | `tests/test_triage_pipeline.py::test_no_strong_model_never_escalates_even_at_severity_5` | `FakeLLMClient([VALID5])` (sev 5) with `strong_model=None` → `len(fake.calls) == 1`, `outcome.model == "fake-model"`, `model_primary is None`, `escalated_model is False`. Mechanical definition of "unchanged": every pre-existing test in `tests/test_triage_pipeline.py` and `tests/test_tool_loop.py` passes without edits to its assertions (only the helper lift moves code) |
| escalate with tools | `tests/test_tool_loop.py::test_severity_escalation_runs_the_strong_model_over_the_same_conversation` | fake `[[ScriptedToolCall("echo", {"x": 1})], VALID4, VALID4_STRONG]` with `EchoTool`, `strong_model="strong-model"`, thresholds `(4, 0.6)`: `len(fake.calls) == 3`; call 1 = the tool turn (cheap), call 2 = the cheap verdict, call 3 = the strong pass. `fake.calls[2].model == "strong-model"`, `fake.calls[2].tools is None`; `fake.calls[2].messages == fake.calls[1].messages` (the strong call sees exactly the conversation the cheap final call saw: system + delimited summary + the tool-call turn + the delimited echo result — `FakeCall` snapshots the list per call, so equality is exact); no message `content` in call 3 contains `VALID4`'s reasoning text (the cheap verdict is never appended); outcome: `verdict.reasoning.startswith("strong tier: ")`, `model == "strong-model"`, `model_primary == "fake-model"`, `escalated_model is True`, `len(tool_calls) == 1`, `input_tokens == 300` (3 × 100), `cost_usd == Decimal("0.000300")`, `retried is False`. **Computed:** 1 tool turn + 1 cheap final + 1 strong = 3 calls |
| confidence path | `tests/test_tool_loop.py::test_low_confidence_escalates_without_tools` | tool-less fake `[VALID3_LOW (sev 3, conf 0.2), VALID3_STRONG]` → 2 calls, second on `"strong-model"`, `escalated_model is True`; INFO log has `reason=confidence` |
| not escalated | `tests/test_tool_loop.py::test_no_escalation_below_both_thresholds` | `[VALID1]` with routing on → 1 call, `escalated_model is False`, `model_primary is None` |
| strong retry | `tests/test_tool_loop.py::test_strong_tier_validation_failure_is_retried_once` | `[VALID4, "{}", VALID4_STRONG]` → 3 calls, `retried is True`, tokens summed over 3 (the failed strong reply's usage counts: `StructuredOutputError` carries it) |
| strong fails twice | `tests/test_tool_loop.py::test_strong_tier_failing_twice_raises_verdict_validation_error` | `[VALID4, "{}", "{}"]` → `VerdictValidationError(attempts=2)`; no fallback |
| strong LLM error | `tests/test_tool_loop.py::test_strong_tier_llm_call_error_propagates` | `[VALID4, LLMCallError("x")]` → `LLMCallError` |
| cap path + routing | `tests/test_tool_loop.py::test_cap_path_then_escalation_keeps_the_final_instruction` | `tool_loop_max_iter=1`: `[[tool], [tool]→ignored…]` — script `[[ScriptedToolCall], VALID4, VALID4_STRONG]`; the strong call's last message is `FINAL_VERDICT_INSTRUCTION` (present once), `escalated_model is True`. **Computed:** cap 1 = 1 scripted turn + 1 forced final (cheap) + 1 strong = 3 calls |
| from_settings | `tests/test_tool_loop.py::test_from_settings_wires_strong_model_and_thresholds` | `Settings(cheap_model="fake-model", strong_model="strong-x", escalate_severity_gte=2, escalate_confidence_lt=0.95, …priced)`: `pipeline.strong_model == "strong-x"`; a `[VALID2, VALID2_STRONG]` run escalates with `reason` severity (gte=2 consumed: 2 ≥ 2) and a `[VALID1, VALID1_STRONG]` run escalates with `reason` confidence (lt=0.95 consumed: 0.9 < 0.95) — both strong calls carry `model == "strong-x"`; distinguishable values per seam (rule 7) |
| same id | `tests/test_tool_loop.py::test_from_settings_rejects_strong_equal_to_cheap` | `strong_model="fake-model"` → `ConfigError` mentioning `STRONG_MODEL` |
| persisted routing fields | `tests/test_triage_alert.py::test_escalated_attempt_persists_both_models` | `triage_attempt` with routing on → the verdict row has `model_primary == "fake-model"`, `model_final == "strong-model"`, `escalated_model is True`, `input_tokens == 200`; a non-escalated run → `model_primary == model_final == "fake-model"`, `escalated_model is False` |
| settings | `tests/test_routing.py::test_routing_settings_defaults_and_bounds` | literals with a comment: `4` / `0.6`; `escalate_severity_gte=0` and `=6`, `escalate_confidence_lt=1.5` → `ValidationError`; roster green after graduation |
| scoring | `tests/test_scoring.py::test_escalation_rate_counts_escalated_over_all_cases` | 4 results, 2 escalated, 1 failed (not escalated) → `0.5`; `n_cases == 0` → `0.0`; `COLUMNS.index("escalation_rate") == COLUMNS.index("critical_rec") + 1` (the header cell is the abbreviated `critical_rec`; the briefing draft wrote the `RunMetrics` field name — test-author judgment call 1, accepted; `critical_rec` is NOT renamed); the new header cell is the full word `escalation_rate` so it cannot be confused with the verdict-flag columns `esc_prec`/`esc_rec`; `test_format_table_one_row_per_result_with_headers` re-pinned for the extra column |
| evals flag | `tests/test_evals_run.py::test_strong_model_flag_flows_to_the_pipeline_and_the_table` | `main([... --strong-model strong-model], llm=fake)` with a fake scripted so 1 of N cases escalates → exit 0; some `fake.calls[i].model == "strong-model"`; the printed table's `escalation_rate` cell equals `1/N` formatted like the other ratios |
| evals price check | `tests/test_evals_run.py::test_unpriced_strong_model_exit_1_before_any_case` | real-client path (`llm=None`), `--strong-model ghost` → `1`, stderr `error: config_error: …ghost…`, zero cases run (no result JSON written) |
| CLI | `tests/test_triage_one.py::test_strong_model_flag_escalates_and_prints_routing_fields` | `main([path, "--strong-model", "strong-model"], llm=fake)` → stdout JSON has `"model_primary": "fake-model"`, `"escalated_model": true`, `"model": "strong-model"`; without the flag and `STRONG_MODEL` unset → `escalated_model` false |
| helper lift | `tests/test_tool_loop.py`, `tests/test_tool_loop_db.py`, `tests/test_tool_registry.py` | the local `EchoTool`/`BoomTool`/`_registry`/`_minimal_alert` definitions are DELETED and imported from `tests.helpers` (`EchoTool`, `BoomTool`, `make_registry`, `minimal_alert`); no test assertion changes; `grep -c "class EchoTool" tests/*.py` → exactly 1 (in `helpers.py`) |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2; the **implementer** does Steps 3–7.

- [ ] **Step 1 (RED — test-author): helper lift first** (a pure move: `tests/helpers.py` gains the
  four names; the three loop/registry files import them; run those three files → still green),
  **then** the routing tests per the table across the new and re-opened files; graduate the two
  names from `_SCHEDULED`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q tests/test_routing.py
  tests/test_tool_loop.py tests/test_triage_pipeline.py tests/test_triage_alert.py
  tests/test_scoring.py tests/test_evals_run.py tests/test_triage_one.py
  tests/test_env_example_roster.py` → Expected: `ModuleNotFoundError: worker.routing`; `TypeError:
  __init__() got an unexpected keyword argument 'strong_model'`; scoring fails on the missing
  column/field; the roster test fails until the two fields exist. Pin, commit `test(worker,evals):
  two-tier routing RED, helper lift (m5 task-03)`.
- [ ] **Step 3 (GREEN — implementer): settings + `.env.example` + `worker/routing.py` +
  `worker/outcome.py` fields.** `uv run mypy` clean.
- [ ] **Step 4 (GREEN — implementer): `worker/triage.py`** — parameterise the three closures by
  model, add the routing step, `from_settings`, `triage_attempt`'s two kwargs. The existing loop
  tests must stay green with no assertion edits. `uv run mypy` clean.
- [ ] **Step 5 (GREEN — implementer): `evals/scoring.py`, `evals/run.py`, `worker/triage_one.py`,
  `scripts/seed_dev.py` (`--live` only), README Evaluation paragraph.**
- [ ] **Step 6 (docs — implementer): PRD v1.4 bullet (§6.2 bound, §6.4 sentences).**
- [ ] **Step 7 (implementer): live routing evidence** (only if `STRONG_MODEL` + its price are in
  the local `.env`; otherwise write "owner input pending" in the report and the controller runs it
  at the gate): `uv run python -m worker.triage_one fixtures/alerts/alert5.json` → the printed JSON
  has `escalated_model: true` and `model` = the strong id; `fixtures/alerts/alert1.json` →
  `false`. Paste both (they contain no secrets). Full gates (cold) → commit `feat(worker,evals):
  two-tier routing with config thresholds, both models recorded, escalation rate (m5 task-03)`
  with the two trailers; path-scoped `git add`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_routing.py tests/test_tool_loop.py tests/test_tool_loop_db.py tests/test_tool_registry.py tests/test_triage_pipeline.py tests/test_triage_alert.py tests/test_scoring.py tests/test_evals_run.py tests/test_triage_one.py tests/test_seed_dev.py   # all pass, 0 skipped
grep -c "class EchoTool" tests/*.py | grep -v ':0'                      # tests/helpers.py:1 only
grep -nE '"(gpt|claude|o[0-9])[^"]*"|(severity_gte|confidence_lt) *= *[0-9]' worker/triage.py worker/routing.py ; echo "exit=$?"   # exit=1 — no quoted model id and no threshold assigned from a numeric literal in the pipeline/routing (message text such as "must be >= 1" is not a threshold — review M2, re-review N1)
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
```

## Acceptance

- Every alert goes to the cheap model first; iff `severity >= ESCALATE_SEVERITY_GTE` or
  `confidence < ESCALATE_CONFIDENCE_LT` (settings, never literals) and `STRONG_MODEL` is set, the
  strong model answers once over the same conversation with no tools and its verdict is the one
  persisted with `model_primary` = cheap, `model_final` = strong, `escalated_model = true`;
  otherwise the M4 behaviour is byte for byte unchanged.
- Tokens, cost and latency sum across both tiers; the tool trace is the cheap pass's; strong-tier
  failures fail the attempt (no fallback); `STRONG_MODEL == CHEAP_MODEL` refuses to boot.
- `evals.run` and `worker.triage_one` take `--strong-model`; the eval table has an
  `escalation_rate` column; PRD §6.2's bound and §6.4 describe the implemented behaviour (v1.4).
