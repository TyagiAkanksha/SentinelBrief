---
id: task-04
milestone: m2-service-persistence
depends_on: [task-03]
status: planned
spec: PRD.md §6.2 (one transaction per verdict write), §6.5 (second failure -> failed), §12 M2 (inline triage); CONVENTIONS.md §2 (contract 3 exception), §3
---

# task-04 — `persist_verdict` (one transaction), `TriagePipeline.triage_alert`, inline wiring

## Goal

`worker/store.py::persist_verdict` writes the verdict row, its (empty until M4) tool-call rows,
and the `triaged` status as one unit that the caller commits once; `TriagePipeline.triage_alert`
loads the alert, runs the M0 pipeline, persists, commits, and on validation/LLM failure marks the
alert `failed` in its own transaction and returns the status; `api/main.py` wires the real
pipeline into `create_app(triage=...)` under the documented M2-only import-linter exception; a
signed POST through the real app produces rows in both tables.

## Context (read ONLY these)

- `PRD.md` §6.2, §6.5, §12 M2.
- `CONVENTIONS.md` §2 (contract 3 `ignore_imports` for `api.main` only), §3 (one transaction).
- `.claude/rules/{worker,api}.md`.
- `worker/triage.py`, `worker/llm_client.py` (M0); `core/services/alerts.py` (task-03);
  `core/models/verdicts.py`, `core/models/tool_calls.py` (task-01); `api/main.py` (task-02).

## Files

- Create: `worker/store.py`, `tests/test_store.py`, `tests/test_inline_triage.py`
- Modify: `worker/triage.py` (+ `triage_alert`), `api/main.py` (guards + wiring),
  `pyproject.toml` (contract 3 `ignore_imports`)

## Interfaces

- **Consumes:** `TriagePipeline.run`, `TriageOutcome`, `OpenAICompatibleLLMClient.from_settings`,
  `VerdictRow`, `ToolCallRow`, `AlertStatus`, `get_alert`, `set_alert_status`, `SessionAlert`,
  `VerdictValidationError`, `LLMCallError`, `create_app`, `FakeLLMClient`.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # worker/store.py
  @dataclass(frozen=True)
  class ToolCallRecord:
      seq: int; tool_name: str; arguments: dict[str, Any]; result: dict[str, Any]; latency_ms: int
  async def persist_verdict(session: AsyncSession, *, alert_id: uuid.UUID, outcome: TriageOutcome,
                            model_primary: str, escalated_model: bool = False,
                            tool_calls: Sequence[ToolCallRecord] = ()) -> uuid.UUID: ...
      # session.add(VerdictRow(alert_id=..., severity=outcome.verdict.severity, ..., model_primary=model_primary, model_final=outcome.model,
      #   escalated_model=escalated_model, prompt_version=outcome.prompt_version, input_tokens=..., output_tokens=..., cost_usd=..., latency_ms=...));
      # await session.flush(); add ToolCallRow per record; await set_alert_status(session, alert_id, "triaged"); await session.flush(); return verdict.id
      # NO commit here — the caller owns exactly one transaction (PRD §6.2)

  # worker/triage.py addition
  class TriagePipeline:
      async def triage_alert(self, session: AsyncSession, alert_id: uuid.UUID) -> AlertStatus: ...
          # row = await get_alert(session, alert_id); alert = SessionAlert.model_validate(row.raw)
          # try: outcome = await self.run(alert); await persist_verdict(session, alert_id=alert_id, outcome=outcome, model_primary=self.model); await session.commit(); return "triaged"
          # except (VerdictValidationError, LLMCallError) as e: await session.rollback(); await set_alert_status(session, alert_id, "failed"); await session.commit();
          #        logger.warning("triage failed alert_id=%s code=%s", alert_id, e.code); return "failed"

  # api/main.py additions — _require_nonempty(LLM_API_KEY), _require_nonempty(CHEAP_MODEL);
  #   llm = OpenAICompatibleLLMClient.from_settings(settings); pipeline = TriagePipeline(llm=llm, model=settings.cheap_model, prompt_version=settings.triage_prompt_version)
  #   app = create_app(session_factory=session_factory, settings=settings, triage=pipeline.triage_alert)

  # pyproject.toml — contract "api never imports worker or core.llm" gains:
  #   ignore_imports = ["api.main -> worker.triage", "api.main -> worker.llm_client"]   # M2 inline triage; remove at M5
  ```

## Steps (TDD)

- [ ] **Step 1: Write failing tests.** `tests/test_store.py` (DB):
  `test_persist_verdict_writes_row_and_sets_triaged`, `test_persist_verdict_is_all_or_nothing`
  (pass a `ToolCallRecord` with `seq=None`-like invalid data that violates NOT NULL → the flush
  raises; after rollback there is no verdict row and the alert is still `pending`).
  `tests/test_inline_triage.py` (DB; real `TriagePipeline(llm=FakeLLMClient([...]),
  model="fake-model", prompt_version="triage-v1")` passed as `triage=pipeline.triage_alert` to
  `create_app`): `test_signed_post_creates_alert_and_verdict_rows` (counts 1 and 1; verdict's
  `prompt_version == "triage-v1"`), `test_response_reports_triaged_status`,
  `test_second_validation_failure_marks_failed_returns_202` (`["{}", "{}"]` → 202 with
  `status == "failed"`; no verdict row; alert `failed`), `test_llm_call_error_marks_failed`,
  `test_duplicate_post_leaves_verdict_count_unchanged` (fake `calls` unchanged by the second
  POST).
- [ ] **Step 2: Run to see them fail** → Expected: `ModuleNotFoundError: worker.store`;
  `AttributeError: triage_alert`.
- [ ] **Step 3: Implement `worker/store.py` and `triage_alert`.**
- [ ] **Step 4: Wire `api/main.py`; add the `ignore_imports` lines with the comment; `uv run
  lint-imports` → 5 kept.** Ritual: temporarily import `worker.triage` from `api/routes/alerts.py`
  → contract breaks → revert.
- [ ] **Step 5: Tests pass (export line), mypy clean.**
- [ ] **Step 6: Full gates → commit:**
  `feat(worker): one-transaction verdict persistence and inline triage wiring (m2 task-04)`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_store.py tests/test_inline_triage.py     # 7 passed
uv run lint-imports                                                   # Contracts: 5 kept, 0 broken.
grep -n "ignore_imports" pyproject.toml                               # the two api.main lines with the "remove at M5" comment
```

## Acceptance

- A verdict row never exists without its status update and vice versa (all-or-nothing test).
- A signed POST through the real app with a fake LLM yields one `alerts` row (`triaged`) and one
  `verdicts` row; validation failure yields `failed` and no verdict row; duplicates never
  re-triage.
- Only `api.main` imports `worker`, under a commented, dated exception.
