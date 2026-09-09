---
id: task-01
milestone: m4-tool-calling
depends_on: []
status: planned
spec: PRD.md §6.3 (the model decides which tools to call, the worker executes them, results are truncated to a per-tool budget before being fed back), §6.5 (the final reply is still the `Verdict` JSON object), §7.2 (recorded tool-result fixtures for deterministic evals), §10.1 (LLM calls only in `worker/`), §10.6 (tool results are attacker data); CONVENTIONS.md §2 (`core.llm` carries no SDK import; `worker/llm_client.py` is the only `openai` importer), §7 (bounds are Settings), §10 (`FakeLLMClient` is the only LLM double; tool tests replay `tests/fixtures/tools/`); `.claude/rules/{worker,tests,core}.md`
---

# task-01 — Tool-calling seam on `LLMClient` (`complete_with_tools`), `FakeLLMClient` tool scripts, `worker/tools/` (`Tool` Protocol, `ToolContext`, `ToolRegistry` with the `TOOL_RESULT_MAX_CHARS` backstop, `ToolRecorder` live/replay, fixture format)

## Goal

The seam every later M4 task builds on, with no concrete tool and no loop yet. `core.llm.LLMClient`
grows one method, `complete_with_tools`, that returns **either** a `ToolCallTurn` (the model asked
for one or more tools) **or** the usual `LLMResult[Verdict]` (the model answered) — one Protocol,
not a second one, so `TriagePipeline(llm=…)` and every `main(argv, *, llm=…)` seam keep their
type. `OpenAICompatibleLLMClient` implements it through the same price-before-spend / usage /
error path as `complete_structured`; `FakeLLMClient` replays scripted tool-call turns so the loop
(task-06) and every tool can be driven without a network. `worker/tools/` defines the `Tool`
Protocol and `ToolContext`, a `ToolRegistry` that exposes OpenAI-style specs and executes a tool by
name (an unknown name, or a tool that raises, is `{unavailable: true}` — an exception never leaves
the registry: controller ruling Q6, spine M4-a), the one character-budget
backstop `TOOL_RESULT_MAX_CHARS` applied to every serialized result, and the `ToolRecorder` seam:
`LiveToolRecorder` executes (and, given `record_dir`, records) while `ReplayToolRecorder` serves
*external* tools from `tests/fixtures/tools/<tool>/<key>.json` and runs local tools live. The
fixture format and its key derivation are fixed here; tasks 03–05 add fixtures, task-06 wires the
replay into `evals.run` (PRD §7.2).

## Context (read ONLY these)

- `PRD.md` §6.3, §6.5, §7.2, §10.1, §10.6.
- `docs/plans/m4-tool-calling.md` — Global Constraints (tool failures never raise; budgets are
  settings; recorded fixtures, never live APIs in tests).
- `CONVENTIONS.md` §2 (contract 3 — `core.llm` has no SDK import), §4 (the registry's single
  `except Exception` is a ruled exception — controller ruling Q6 — at the worker's boundary with
  third-party code fed attacker-influenced input; routes keep the rule), §7, §10;
  `.claude/rules/worker.md`, `.claude/rules/tests.md`, `.claude/rules/core.md`.
- Code you build on: `core/llm.py` (`ChatMessage`, `LLMUsage`, `LLMResult`, `LLMClient`,
  `parse_structured`, `compute_cost_usd`), `worker/llm_client.py` (`OpenAICompatibleLLMClient` —
  the `cast` boundary comment, price-before-spend, `LLMCallError` mapping), `core/errors.py`
  (`LLMCallError`, `StructuredOutputError`, `ConfigError`), `core/config.py` (`Settings`),
  `core/cache.py` (the `clock=` seam pattern), `core/schemas/alert.py` (`SessionAlert`),
  `tests/fakes.py` (`FakeLLMClient`, `FakeCall`), `tests/test_llm_client.py`
  (`_chat_completion_body`, `_mock_client` — the `httpx.MockTransport` pattern to copy),
  `tests/test_env_example_roster.py`, `.env.example`.
- openai SDK 3.8.0 (uv.lock): a tool-calling reply is `choices[0].message.tool_calls`, a list
  whose members have `.type` (`"function"` or `"custom"`), `.id`, and `.function.name` /
  `.function.arguments` (a JSON **string**); the request takes `tools=[{"type": "function",
  "function": {"name", "description", "parameters"}}]` and `tool_choice="auto"`.

## Files

- Create: `worker/tools/__init__.py`, `worker/tools/base.py`, `worker/tools/recorder.py`,
  `worker/tools/registry.py`, `tests/fixtures/tools/README.md`
- Create (test-author): `tests/test_llm_tools_seam.py`, `tests/test_tool_registry.py`,
  `tests/test_tool_recorder.py`
- Modify (test-author, re-pinned): `tests/fakes.py`
- Modify: `core/llm.py`, `worker/llm_client.py`, `core/config.py`, `.env.example`

## Interfaces

- **Consumes:** `ChatMessage`, `LLMUsage`, `LLMResult`, `LLMClient`, `parse_structured`,
  `compute_cost_usd` (`core.llm`); `LLMCallError`, `StructuredOutputError`, `ConfigError`
  (`core.errors`); `ModelPrice`, `Settings` (`core.config`); `SessionAlert`
  (`core.schemas.alert`); `AsyncSession` (SQLAlchemy); `FakeLLMClient`, `FakeCall`
  (`tests.fakes`).
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/llm.py — additions; nothing existing changes shape except ChatMessage (below)
  class ToolCallFunctionWire(TypedDict):
      name: str
      arguments: str                       # JSON text — exactly what the provider sends/expects
  class ToolCallWire(TypedDict):
      id: str
      type: Literal["function"]
      function: ToolCallFunctionWire
  class ChatMessage(TypedDict):
      role: Literal["system", "user", "assistant", "tool"]     # was: no "tool"
      content: str
      tool_calls: NotRequired[list[ToolCallWire]]              # an assistant turn that requested tools
      tool_call_id: NotRequired[str]                           # a tool-result turn; echoes ToolCallRequest.id
      # Existing {"role": ..., "content": ...} literals stay valid (NotRequired keys).
  class ToolSpec(TypedDict):                                   # provider-neutral tool definition
      name: str
      description: str
      parameters: dict[str, Any]                               # a JSON Schema object
  @dataclass(frozen=True)
  class ToolCallRequest:
      id: str
      name: str
      arguments: dict[str, Any]                                # the decoded JSON object
  @dataclass(frozen=True)
  class ToolCallTurn:                                          # "the model asked for tools"
      calls: tuple[ToolCallRequest, ...]                       # len >= 1
      model: str
      usage: LLMUsage
      cost_usd: Decimal
      latency_ms: int
  def tool_calls_message(calls: Sequence[ToolCallRequest]) -> ChatMessage: ...
      # {"role": "assistant", "content": "", "tool_calls": [{"id": c.id, "type": "function",
      #   "function": {"name": c.name, "arguments": json.dumps(c.arguments, sort_keys=True)}} for c in calls]}
      # — the assistant turn a caller appends before the tool-result turns; built here so the real
      #   client, the fake, and the pipeline agree on one wire shape

  class LLMClient(Protocol):
      async def complete_structured[T: BaseModel](self, *, messages, response_model, model) -> LLMResult[T]: ...   # unchanged
      async def complete_with_tools[T: BaseModel](
          self, *, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], response_model: type[T], model: str
      ) -> LLMResult[T] | ToolCallTurn: ...
          # tools must be non-empty (ValueError otherwise — callers only ask for tools when a registry has some);
          # a content reply is validated exactly like complete_structured (StructuredOutputError on failure);
          # Raises LLMCallError / ConfigError / StructuredOutputError with the same meanings as complete_structured

  # worker/llm_client.py::OpenAICompatibleLLMClient.complete_with_tools — behavior
  #   request: model, messages (cast at the SDK boundary as today), temperature=0.0, the SAME response_format as
  #     complete_structured (json_object / json_schema per settings), tools=[{"type": "function", "function": spec}
  #     for spec in tools], tool_choice="auto"
  #   price check BEFORE the call (ConfigError for an unpriced model — no provider call), openai/httpx errors ->
  #     LLMCallError, missing usage -> LLMCallError, exactly as complete_structured; share one private helper for
  #     the request/usage/error path so the two methods cannot drift
  #   reply: if message.tool_calls is non-empty ->
  #       every member must have type == "function" else LLMCallError("unsupported tool call type: <type>");
  #       json.loads(function.arguments) must be a JSON object else LLMCallError("tool call arguments are not a JSON object");
  #       return ToolCallTurn(calls=(ToolCallRequest(id, name, arguments), ...) in reply order, model, usage,
  #                           cost_usd=compute_cost_usd(price, usage), latency_ms)
  #     else -> content empty -> LLMCallError("provider returned no content"); else parse_structured(content, ...)

  # tests/fakes.py — additions (test-author-owned, pinned)
  @dataclass(frozen=True)
  class ScriptedToolCall:
      name: str
      arguments: dict[str, Any]
      id: str = ""                                             # "" -> the fake mints f"call_{turn}_{index}" (both 1-based:
                                                               #   turn = position of this reply in fake.calls, index = position in the turn)
  FakeReply = str | Exception | Sequence[ScriptedToolCall]     # str -> validated through parse_structured (unchanged);
                                                               # Exception -> raised; a sequence of ScriptedToolCall -> a ToolCallTurn
  @dataclass
  class FakeCall:
      messages: list[ChatMessage]; response_model: type[BaseModel]; model: str
      tools: list[ToolSpec] | None                             # None for complete_structured; the specs for complete_with_tools
  class FakeLLMClient:
      def __init__(self, responses: Sequence[FakeReply], *, usage=LLMUsage(100, 50), cost_usd=Decimal("0.000100"), latency_ms=5) -> None: ...
      async def complete_structured(...)                       # unchanged, except: a scripted tool-call reply here raises
                                                               #   AssertionError("FakeLLMClient: tool calls were scripted but complete_structured was called")
      async def complete_with_tools(...) -> LLMResult[T] | ToolCallTurn
          # records FakeCall(tools=list(tools)); pops the next reply: str -> parse_structured (as complete_structured);
          # Exception -> raise; ScriptedToolCall sequence -> ToolCallTurn(calls, model, self._usage, self._cost_usd, self._latency_ms);
          # exhausted -> AssertionError("FakeLLMClient: no responses left")
      # RED-safety: `tests/fakes.py` is imported by most of the suite. Import ToolCallRequest/ToolCallTurn/ToolSpec from
      # core.llm under `if TYPE_CHECKING:` for annotations and import them *locally inside* complete_with_tools for the
      # runtime constructor, so the RED commit fails only the new tests (ImportError at call time), never the whole
      # suite at collection. Leave that pattern in place after GREEN (the file is pinned).

  # worker/tools/base.py
  @dataclass(frozen=True)
  class ToolContext:
      alert: SessionAlert                                      # the session being triaged — get_session_commands reads it
      session: AsyncSession | None                             # the triage transaction's session; None outside triage_alert (CLI, evals)
      now: datetime                                            # aware UTC; injected so windows and caches are deterministic in tests
  class Tool(Protocol):
      name: str
      description: str                                         # what the model reads when deciding to call it
      parameters: dict[str, Any]                               # JSON Schema object for `arguments`
      external: bool                                           # True: result depends on something outside the alert + repo (network, a
                                                               #   downloaded DB, the alerts table) -> ReplayToolRecorder serves it from a
                                                               #   fixture; False: deterministic from ctx.alert + repo files -> runs live everywhere
      async def run(self, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]: ...
          # NEVER raises by contract: every failure is `unavailable(reason)` (spine constraint M4-a); the tool validates its own
          # arguments. The registry's backstop below catches whatever slips through anyway.
  def unavailable(reason: str) -> dict[str, Any]: ...          # {"unavailable": True, "reason": reason}
  def spec_for(tool: Tool) -> ToolSpec: ...                    # {"name": tool.name, "description": tool.description, "parameters": tool.parameters}

  # worker/tools/recorder.py
  FIXTURE_KEY_CHARS = 16
  def fixture_key(arguments: Mapping[str, Any]) -> str: ...
      # sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()[:FIXTURE_KEY_CHARS]
      # e.g. {"ip": "203.0.113.10"} -> canonical '{"ip":"203.0.113.10"}' -> "5d2e7bda8feb939e" (verified at briefing time)
  def fixture_path(root: Path, tool_name: str, arguments: Mapping[str, Any]) -> Path: ...    # root / tool_name / f"{fixture_key(arguments)}.json"
  def write_fixture(root: Path, tool_name: str, arguments: Mapping[str, Any], result: Mapping[str, Any]) -> Path: ...
      # mkdir parents; writes json.dumps({"tool": tool_name, "arguments": dict(arguments), "result": dict(result)}, indent=2, sort_keys=True) + "\n"
  class ToolRecorder(Protocol):
      async def execute(self, tool: Tool, arguments: Mapping[str, Any], ctx: ToolContext) -> dict[str, Any]: ...
  class LiveToolRecorder:
      def __init__(self, *, record_dir: Path | None = None) -> None: ...
      async def execute(...): result = await tool.run(arguments, ctx); if record_dir is not None: write_fixture(record_dir, tool.name, arguments, result); return result
  class ReplayToolRecorder:
      def __init__(self, fixtures_dir: Path) -> None: ...
      async def execute(...):
          # not tool.external -> await tool.run(arguments, ctx)                       (local tools are deterministic; no fixture needed)
          # path = fixture_path(fixtures_dir, tool.name, arguments); missing -> unavailable("fixture_missing") + logger.warning once per path
          # json.loads fails / OSError -> unavailable("fixture_unreadable")
          # data["tool"] != tool.name or data["arguments"] != dict(arguments) -> unavailable("fixture_mismatch")   (guards a stale key)
          # else -> dict(data["result"])
          # Never calls tool.run for an external tool — that is the whole point (PRD §7.2, CLAUDE.md "never live APIs").

  # worker/tools/registry.py
  @dataclass(frozen=True)
  class ToolExecution:
      result: dict[str, Any]                                   # already truncated — exactly what the model will see and what is persisted
      latency_ms: int
  def truncate_result(result: Mapping[str, Any], max_chars: int) -> dict[str, Any]: ...
      # serialized = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
      # len(serialized) <= max_chars -> dict(result) unchanged
      # else -> {"truncated": True, "original_chars": len(serialized), "preview": serialized[:max_chars]}
      #   (a valid JSON object whose preview is plain text: the model sees the head of the payload, the trace stays valid JSONB)
  class ToolRegistry:
      def __init__(self, tools: Sequence[Tool], *, recorder: ToolRecorder, max_result_chars: int,
                   clock: Callable[[], float] = time.perf_counter) -> None: ...
          # ValueError on a duplicate tool name or max_result_chars < 1; `clock` is the latency seam (CONVENTIONS §10)
      @property
      def names(self) -> tuple[str, ...]: ...                  # registration order
      def specs(self) -> list[ToolSpec]: ...                   # [spec_for(t) for t in tools], registration order
      async def execute(self, name: str, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolExecution: ...
          # unknown name -> ToolExecution(unavailable("unknown_tool"), latency_ms=0) — the loop still records the call
          # else start = clock()
          #      try: result = await recorder.execute(tool, arguments, ctx)
          #      except Exception as exc:                       # the ONE deliberate backstop (controller ruling Q6; spine M4-a): inline triage
          #          # persists until M5, so an exception escaping here would 500 the ingest request and strand the alert `pending`
          #          logger.exception("tool raised tool=%s arg_keys=%s", name, sorted(arguments))   # keys only — values are attacker-influenced
          #          result = unavailable(f"{type(exc).__name__}: tool raised")
          #      latency_ms = int((clock() - start) * 1000)
          #      return ToolExecution(truncate_result(result, max_result_chars), latency_ms)
          # `asyncio.CancelledError` is a BaseException and is NOT caught — cancellation must propagate.

  # worker/tools/__init__.py — re-exports: Tool, ToolContext, unavailable, spec_for, ToolRecorder, LiveToolRecorder,
  #   ReplayToolRecorder, fixture_key, fixture_path, write_fixture, FIXTURE_KEY_CHARS, ToolRegistry, ToolExecution, truncate_result

  # core/config.py — new field (+ .env.example line under "Enrichment tools (from M4)")
  tool_result_max_chars: Annotated[int, Field(ge=1)] = 4000
  # TOOL_RESULT_MAX_CHARS=4000
  # Decision (recorded in the briefing report): ONE character backstop for every tool, not five TOOL_RESULT_MAX_CHARS_<TOOL>
  # settings. PRD §6.3's per-tool budget is structural and lives in each tool ("max 40 commands, summarized count beyond"
  # -> task-02's TOOL_SESSION_COMMANDS_MAX / TOOL_SESSION_DOWNLOADS_MAX / TOOL_COMMAND_MAX_CHARS); the character cap only keeps any
  # single result from flooding the context.
  # One safety net is enough; five untuned knobs are a maintenance cost with no consumer.

  # tests/fixtures/tools/README.md — the fixture contract, stated once for tasks 03–05 and M7:
  #   layout tests/fixtures/tools/<tool_name>/<key>.json; key = fixture_key(arguments) (rule above, with the worked example);
  #   file body {"tool", "arguments", "result"}; how to mint one (`LiveToolRecorder(record_dir=Path("tests/fixtures/tools"))`
  #   or `write_fixture`); the one-liner to compute a key by hand:
  #     uv run python -c "from worker.tools import fixture_key; print(fixture_key({'ip': '203.0.113.10'}))"   # 5d2e7bda8feb939e
  #   and the rule: fixtures hold the tool's *normalized result*, never a raw provider body (rules/tests.md); M4's fixtures are
  #   synthetic (documentation IPs, RFC 5398 ASNs).
  ```

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `ChatMessage` tool role + `tool_calls_message` | `tests/test_llm_tools_seam.py::test_tool_calls_message_builds_the_assistant_wire_shape` | two requests → role `assistant`, content `""`, two `tool_calls` with `type == "function"` and `function.arguments == json.dumps(args, sort_keys=True)`; **fails when** `arguments` is emitted as a dict, when `id` is dropped, or when order changes |
| `ToolCallRequest` / `ToolCallTurn` frozen | `tests/test_llm_tools_seam.py::test_tool_call_types_are_frozen` | assigning `turn.calls = ()` raises `FrozenInstanceError`; fails when either dataclass is mutable |
| real client request shape | `tests/test_llm_tools_seam.py::test_complete_with_tools_sends_tools_tool_choice_json_mode_and_temperature_zero` | `MockTransport` captures the JSON body: `tools[0] == {"type": "function", "function": spec}`, `tool_choice == "auto"`, `response_format == {"type": "json_object"}`, `temperature == 0.0`, messages forwarded; fails when any of the four is missing or `tools` is wrapped differently |
| real client tool-call reply → `ToolCallTurn` | `tests/test_llm_tools_seam.py::test_complete_with_tools_returns_tool_call_turn_with_decoded_arguments_usage_and_cost` | body with two function calls → `isinstance(turn, ToolCallTurn)`, `turn.calls[0].arguments == {"session_id": "abc"}` (decoded), ids preserved, `usage == LLMUsage(100, 50)`, `cost_usd == compute_cost_usd(price, usage)`; fails when arguments stay a string or cost is `0` |
| real client content reply | `tests/test_llm_tools_seam.py::test_complete_with_tools_content_reply_validates_like_complete_structured` | a valid verdict body → `LLMResult` with `parsed.category == "brute_force"`; `"not json"` → `StructuredOutputError` carrying usage; fails when the content path bypasses `parse_structured` |
| non-object arguments | `tests/test_llm_tools_seam.py::test_complete_with_tools_rejects_non_object_arguments` | `arguments: "[1, 2]"` and `arguments: "{"` each → `LLMCallError`; fails when `[]`/malformed text is passed through as `{}` |
| non-function tool call | `tests/test_llm_tools_seam.py::test_complete_with_tools_rejects_non_function_tool_call_type` | a `type: "custom"` member → `LLMCallError` mentioning `custom` |
| empty `tools` | `tests/test_llm_tools_seam.py::test_complete_with_tools_requires_at_least_one_tool` | `tools=[]` → `ValueError` before any HTTP request (transport handler asserts it was never called) |
| price before spend | `tests/test_llm_tools_seam.py::test_complete_with_tools_unpriced_model_never_calls_provider` | `ConfigError`; transport never invoked |
| HTTP failure | `tests/test_llm_tools_seam.py::test_complete_with_tools_maps_http_500_to_llm_call_error` | `max_retries=0` mock client, 500 → `LLMCallError` |
| missing usage | `tests/test_llm_tools_seam.py::test_complete_with_tools_missing_usage_raises_llm_call_error` | body without `usage` → `LLMCallError` |
| fake tool script | `tests/test_llm_tools_seam.py::test_fake_replays_scripted_tool_calls_with_minted_ids_and_records_tools` | `FakeLLMClient([[ScriptedToolCall("get_session_commands", {"session_id": "s"})], VALID_VERDICT_JSON])` → first `complete_with_tools` returns a `ToolCallTurn` whose call id is `call_1_1` and `fake.calls[0].tools == list(tools)`; second returns `LLMResult`; fails when ids collide across turns (`call_2_1` for a second scripted turn) or `tools` is not recorded |
| fake explicit id | `tests/test_llm_tools_seam.py::test_fake_keeps_an_explicit_scripted_id` | `ScriptedToolCall(..., id="abc")` → `calls[0].id == "abc"` |
| fake refuses script on the wrong method | `tests/test_llm_tools_seam.py::test_fake_rejects_tool_script_on_complete_structured` | `complete_structured` with a scripted tool reply queued → `AssertionError` |
| fake exhausted | `tests/test_llm_tools_seam.py::test_fake_complete_with_tools_exhausted_raises` | empty queue → `AssertionError("FakeLLMClient: no responses left")` |
| fake `complete_structured` unchanged | `tests/test_llm_tools_seam.py::test_fake_complete_structured_records_tools_none` | `fake.calls[0].tools is None` |
| `spec_for` / `unavailable` | `tests/test_tool_registry.py::test_spec_for_and_unavailable_shapes` | `spec_for(tool)` has exactly the three keys; `unavailable("x") == {"unavailable": True, "reason": "x"}` |
| registry construction | `tests/test_tool_registry.py::test_registry_rejects_duplicate_names_and_nonpositive_budget` | two tools named `echo` → `ValueError`; `max_result_chars=0` → `ValueError` |
| `names` / `specs` order | `tests/test_tool_registry.py::test_registry_names_and_specs_follow_registration_order` | `("a", "b")`, specs in the same order; fails when sorted alphabetically (register `b` then `a`) |
| execute through recorder + latency seam | `tests/test_tool_registry.py::test_registry_executes_through_the_recorder_and_measures_latency` | a recording `ToolRecorder` stub sees `(tool, arguments, ctx)`; `clock` ticking `1.0 → 1.25` → `latency_ms == 250`; fails when the registry calls `tool.run` directly (recorder never sees it) |
| unknown tool | `tests/test_tool_registry.py::test_registry_unknown_tool_is_unavailable_and_never_raises` | `execute("nope", {}, ctx)` → `result == unavailable("unknown_tool")`, `latency_ms == 0`, recorder not called |
| raising tool | `tests/test_tool_registry.py::test_execute_returns_unavailable_when_a_tool_raises` | a registered `BoomTool` whose `run` raises `RuntimeError("boom")`, called with `{"secret_arg": "value-7"}` → `result == unavailable("RuntimeError: tool raised")`, `latency_ms` measured through the clock, exactly one ERROR record in `caplog` whose message contains `boom` (the tool name) and `secret_arg` and does **not** contain `value-7`; no exception; fails when the exception propagates, when a value is logged, or when the log level is below ERROR |
| truncation applied | `tests/test_tool_registry.py::test_registry_truncates_oversized_results_to_the_budget` | a tool returning a 10 000-char string with `max_result_chars=100` → `result["truncated"] is True`, `len(result["preview"]) == 100`, `original_chars > 100`; fails when the untruncated result leaks through |
| `truncate_result` identity + shape | `tests/test_tool_registry.py::test_truncate_result_under_budget_is_identity_and_over_budget_has_preview` | under budget → equal dict (and not the same object); over budget → exactly the three keys, `preview == serialized[:max_chars]`, `ensure_ascii=False` keeps a `é` intact; fails when a non-ASCII char is escaped or `original_chars` counts the pretty form |
| `fixture_key` | `tests/test_tool_recorder.py::test_fixture_key_is_canonical_order_independent_and_16_hex` | `{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` → same 16-hex key; `{"ip": "203.0.113.10"}` → `"5d2e7bda8feb939e"`; fails when whitespace or key order changes the key |
| `fixture_path` | `tests/test_tool_recorder.py::test_fixture_path_layout` | `root/get_ip_geo_asn/<key>.json` |
| `write_fixture` + replay round trip | `tests/test_tool_recorder.py::test_replay_serves_the_recorded_result_for_an_external_tool` | write, then `ReplayToolRecorder(tmp).execute(external_tool, args, ctx)` returns the recorded result and the tool's own `run` was **not** called (counter stays 0); fails when replay falls through to `run` |
| replay runs local tools | `tests/test_tool_recorder.py::test_replay_runs_a_non_external_tool_live` | `external=False` tool → `run` called once, no fixture read |
| replay missing / mismatch / unreadable | `tests/test_tool_recorder.py::test_replay_missing_fixture_is_unavailable`, `::test_replay_mismatched_fixture_is_unavailable`, `::test_replay_unreadable_fixture_is_unavailable` | reasons `fixture_missing` (and `caplog` shows one warning), `fixture_mismatch` (file written for other arguments then renamed onto this key), `fixture_unreadable` (`not json`); none raises |
| live recorder | `tests/test_tool_recorder.py::test_live_recorder_executes_and_records_only_when_record_dir_is_set` | without `record_dir`: `run` called, nothing on disk; with: the fixture file exists with `{"tool", "arguments", "result"}` and `sort_keys` order |
| `tool_result_max_chars` setting | `tests/test_tool_registry.py::test_tool_result_max_chars_setting_default_env_and_bound` | default `4000`; `monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "10")` → `10`; `Settings(tool_result_max_chars=0)` → `ValidationError`; `.env.example` roster test stays green |

Test tools for the registry/recorder tests are tiny `Tool` implementations defined in the test
files (an `EchoTool` returning its arguments, a `BigTool` returning a long string, an
`ExternalStub` with `external=True` and a call counter). They implement our Protocol — they are
the external seam, not a mock of our own code (CONVENTIONS §10).

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 (three new test files and the `tests/fakes.py`
extension) and pins the four files with a sha256; the **implementer** does Steps 3–6 and never
edits a pinned file — it stops and asks the controller if a pinned test looks wrong.

- [ ] **Step 1 (RED — test-author): write `tests/test_llm_tools_seam.py`, `tests/test_tool_registry.py`,
  `tests/test_tool_recorder.py`** per the table, and extend `tests/fakes.py` per Interfaces
  (`ScriptedToolCall`, `FakeReply`, `FakeCall.tools`, `complete_with_tools`, the
  `TYPE_CHECKING` + local-import pattern). `uv run ruff format` and `uv run ruff check` on the
  four files.
- [ ] **Step 2 (RED — test-author): run to see them fail, and the existing suite stay green.**
  `uv run pytest -q tests/test_llm_tools_seam.py tests/test_tool_registry.py tests/test_tool_recorder.py`
  → Expected: `test_tool_registry.py` and `test_tool_recorder.py` error at collection with
  `ModuleNotFoundError: No module named 'worker.tools'`; `test_llm_tools_seam.py` errors with
  `ImportError: cannot import name 'ToolCallTurn' from 'core.llm'`. Then
  `uv run pytest -q tests/test_llm_client.py tests/test_triage_pipeline.py tests/test_evals_run.py tests/test_seed_dev.py`
  → all green (the `tests/fakes.py` change is additive; `FakeCall` gains a field, so check that no
  existing test constructs `FakeCall` positionally — `grep -rn "FakeCall(" tests/` shows only
  `tests/fakes.py` and `tests/test_llm_client.py`'s import). Pin the four files, commit
  `test(worker,core): tool-calling seam, registry, recorder RED (m4 task-01)`.
- [ ] **Step 3 (GREEN — implementer): `core/llm.py` additions** per Interfaces (`NotRequired` from
  `typing`; `tool_calls_message`; `LLMClient.complete_with_tools` with a full docstring). `uv run
  mypy` clean — the fake's `TYPE_CHECKING` imports now resolve.
- [ ] **Step 4 (GREEN — implementer): `worker/llm_client.py::complete_with_tools`** with the shared
  private request helper; the `cast` boundary comment extends to the `tools` param (it is our
  provider-neutral `ToolSpec` wrapped in the SDK's `{"type": "function", "function": …}` shape).
  `uv run mypy` clean; `uv run pytest -q tests/test_llm_tools_seam.py tests/test_llm_client.py`
  green.
- [ ] **Step 5 (GREEN — implementer): `worker/tools/{base,recorder,registry,__init__}.py`**,
  `core/config.py::tool_result_max_chars`, the `.env.example` line, and
  `tests/fixtures/tools/README.md` per Interfaces. Module docstrings cite PRD §6.3/§7.2/§10.6 and
  the "tools never raise" contract. `uv run mypy` clean.
- [ ] **Step 6 (implementer): all tests in the table + the existing suite green with the export
  line set; `uv run lint-imports` still reports 5 kept contracts (`worker.tools` imports only
  `core.*`); full gates → commit:**
  `feat(worker,core): LLMClient.complete_with_tools, FakeLLMClient tool scripts, worker.tools registry + recorder (m4 task-01)`
  with the two trailers from `CONVENTIONS.md` §12. Path-scoped `git add core/llm.py
  worker/llm_client.py worker/tools core/config.py .env.example tests/fixtures/tools/README.md`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_llm_tools_seam.py tests/test_tool_registry.py tests/test_tool_recorder.py   # every test in the table passes
uv run pytest -q tests/test_llm_client.py tests/test_triage_pipeline.py tests/test_inline_triage.py tests/test_evals_run.py tests/test_seed_dev.py   # existing suite green
grep -n "import openai\|from openai" core/llm.py ; echo "exit=$?"                                        # exit=1 — no SDK import in core.llm
uv run python -c "from worker.tools import fixture_key; print(fixture_key({'ip': '203.0.113.10'}))"      # 5d2e7bda8feb939e
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean; "Contracts: 5 kept, 0 broken"
```

## Acceptance

- `LLMClient.complete_with_tools` exists on the Protocol, the real client, and the fake; the real
  client sends `tools` + `tool_choice="auto"` + the configured `response_format` at temperature 0,
  prices before spending, and returns a `ToolCallTurn` with decoded arguments and usage/cost, or
  a validated `LLMResult`, or the same typed errors as `complete_structured`.
- `FakeLLMClient` replays scripted tool-call turns with deterministic ids and records the tool
  specs it was offered; the existing suite is unchanged in behavior.
- `ToolRegistry.execute` never raises (unknown tool → `unavailable("unknown_tool")`; a raising
  tool → `unavailable("<ExceptionClass>: tool raised")` with one ERROR log naming the tool and
  the argument keys only), measures latency through an injectable clock, and truncates every
  result to `TOOL_RESULT_MAX_CHARS`;
  `ReplayToolRecorder` never executes an external tool and reports `fixture_missing` /
  `fixture_mismatch` / `fixture_unreadable` instead of raising; `LiveToolRecorder` records a
  fixture in the documented format when asked; `fixture_key({"ip": "203.0.113.10"})` is
  `5d2e7bda8feb939e`.
