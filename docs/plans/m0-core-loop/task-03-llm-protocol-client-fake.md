---
id: task-03
milestone: m0-core-loop
depends_on: [task-02]
status: planned
spec: PRD.md §4 (LLM row), §6.5 (structured output), §10.1; CONVENTIONS.md §2 (contract 3), §7, §10
---

# task-03 — `LLMClient` Protocol, OpenAI-compatible async client, `FakeLLMClient`

## Goal

`core/llm.py` defines the provider-neutral seam (Protocol, result/usage types,
`parse_structured`, `compute_cost_usd`) with no SDK import; `worker/llm_client.py` is the only
module that imports the `openai` SDK and speaks to any OpenAI-compatible endpoint with
temperature 0 and JSON output; `tests/fakes.py::FakeLLMClient` replays canned replies through the
same `parse_structured` path so bad JSON fails identically in tests and production. An unpriced
configured model is a boot-time `ConfigError`.

## Context (read ONLY these)

- `PRD.md` §4 (LLM row: OpenAI-compatible, `json_object` + schema in prompt), §6.5, §10.1.
- `CONVENTIONS.md` §2 (contract 3 — `core.llm` is forbidden to `api`; only `worker.llm_client`
  imports the SDK), §7 (prices), §10 (fakes validate through the real parse path).
- `core/config.py`, `core/errors.py`, `core/schemas/verdict.py` from task-02.
- AdvisorDesk `apps/api/app/rag/synthesis.py` (read-only) for the Protocol + `from_settings` +
  error-mapping shape.

## Files

- Create: `core/llm.py`, `worker/llm_client.py`, `tests/fakes.py`, `tests/test_llm_client.py`,
  `tests/test_llm_live.py`

## Interfaces

- **Consumes:** `Settings`, `ModelPrice`, `ConfigError`, `LLMCallError`, `StructuredOutputError`,
  `Verdict` (task-02).
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/llm.py  — interface only; MUST NOT import openai
  class ChatMessage(TypedDict):
      role: Literal["system", "user", "assistant"]
      content: str

  @dataclass(frozen=True)
  class LLMUsage:
      input_tokens: int
      output_tokens: int

  @dataclass(frozen=True)
  class LLMResult[T: BaseModel]:
      parsed: T; raw_text: str; model: str; usage: LLMUsage; cost_usd: Decimal; latency_ms: int

  class LLMClient(Protocol):
      async def complete_structured[T: BaseModel](
          self, *, messages: Sequence[ChatMessage], response_model: type[T], model: str
      ) -> LLMResult[T]: ...

  def parse_structured[T: BaseModel](
      raw_text: str, response_model: type[T], *, model: str, usage: LLMUsage,
      cost_usd: Decimal, latency_ms: int,
  ) -> LLMResult[T]: ...            # response_model.model_validate_json; on ValidationError raise
                                    # StructuredOutputError(raw_text=…, validation_error=str(err), input_tokens=…, …)

  def compute_cost_usd(price: ModelPrice, usage: LLMUsage) -> Decimal: ...
      # (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000, quantized to 6 dp

  # worker/llm_client.py — the ONLY module importing openai
  class OpenAICompatibleLLMClient:
      def __init__(self, *, client: openai.AsyncOpenAI, prices: Mapping[str, ModelPrice],
                   json_mode: Literal["json_object", "json_schema"]) -> None: ...
      @classmethod
      def from_settings(cls, settings: Settings) -> OpenAICompatibleLLMClient: ...
          # ConfigError if cheap_model is empty or unpriced; ConfigError if strong_model is set but unpriced;
          # AsyncOpenAI(api_key=settings.llm_api_key.get_secret_value() or "unset", base_url=settings.llm_base_url, timeout=60.0, max_retries=2)
      async def complete_structured[T: BaseModel](self, *, messages, response_model, model) -> LLMResult[T]: ...
          # temperature=0.0; response_format={"type":"json_object"} (json_mode="json_object") or
          # {"type":"json_schema","json_schema":{"name": response_model.__name__, "schema": response_model.model_json_schema(), "strict": True}};
          # openai.OpenAIError | httpx.HTTPError -> LLMCallError; response.usage is None -> LLMCallError("provider returned no usage");
          # latency via time.perf_counter; cost via compute_cost_usd(self._prices[model], usage) — KeyError -> ConfigError

  # tests/fakes.py
  @dataclass
  class FakeCall:
      messages: list[ChatMessage]; response_model: type[BaseModel]; model: str

  class FakeLLMClient:
      def __init__(self, responses: Sequence[str | Exception], *, usage: LLMUsage = LLMUsage(100, 50),
                   cost_usd: Decimal = Decimal("0.000100"), latency_ms: int = 5) -> None: ...
      calls: list[FakeCall]
      async def complete_structured(...) -> LLMResult[T]: ...
          # pops the next response; Exception -> raise it; str -> parse_structured(text, response_model, model=..., usage=..., ...)
          # (so invalid JSON raises StructuredOutputError exactly like the real client); raises AssertionError when exhausted
  ```

## Steps (TDD)

- [ ] **Step 1: Write failing tests** in `tests/test_llm_client.py`:
  `test_parse_structured_returns_model`, `test_parse_structured_raises_with_raw_text_and_usage`
  (bad JSON → `StructuredOutputError` with `.raw_text`, `.validation_error`, `.input_tokens`),
  `test_compute_cost_usd_per_million_quantized` (100k in / 50k out at 0.15/0.60 →
  `Decimal("0.045000")`), `test_from_settings_raises_when_cheap_model_empty`,
  `test_from_settings_raises_when_model_unpriced`,
  `test_complete_structured_sends_json_object_and_temperature_zero` — build
  `AsyncOpenAI(api_key="x", base_url="http://test/v1", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))`
  where `handler` captures the request JSON and returns a chat-completion body with
  `choices[0].message.content` = a valid `Verdict` JSON and `usage`; assert
  `temperature == 0` and `response_format == {"type": "json_object"}`;
  `test_complete_structured_maps_http_500_to_llm_call_error`,
  `test_missing_usage_raises_llm_call_error`, `test_fake_records_calls_and_replays`.
  `tests/test_llm_live.py::test_live_complete_structured_returns_verdict` marked
  `@pytest.mark.live`, skipping with a reason when `Settings().llm_api_key` is empty.
- [ ] **Step 2: Run to see them fail:** `uv run pytest -q tests/test_llm_client.py` → Expected:
  `ModuleNotFoundError: core.llm`.
- [ ] **Step 3: Implement `core/llm.py`** per Interfaces (PEP 695 generics; `parse_structured`
  and `compute_cost_usd` are pure).
- [ ] **Step 4: Implement `worker/llm_client.py`**; `from_settings` builds the SDK client with
  `base_url` from settings; docstring cites PRD §4 and §6.5.
- [ ] **Step 5: Implement `tests/fakes.py`.**
- [ ] **Step 6: Run the tests → pass; `uv run lint-imports` → 5 kept** (contract 3 must still hold:
  nothing under `api/` imports `core.llm`; `worker.llm_client` may).
- [ ] **Step 7: Full gates → commit:**
  `feat(worker): LLM protocol, OpenAI-compatible client, FakeLLMClient (m0 task-03)`.

## Verify

```bash
uv run pytest -q tests/test_llm_client.py                # 9 passed
uv run pytest -q -m live tests/test_llm_live.py          # 1 passed (with LLM_API_KEY + CHEAP_MODEL + prices exported) or 1 skipped
grep -rn "import openai\|from openai" core api evals     # no output — only worker/llm_client.py imports the SDK
uv run mypy && uv run lint-imports                       # clean
```

## Acceptance

- `core/llm.py` has no SDK import; `worker/llm_client.py` is the only module that does.
- A bad JSON reply raises `StructuredOutputError` carrying the raw text through both the real
  client and the fake.
- `from_settings` refuses an empty or unpriced `cheap_model`.
- The live test passes once with the owner's key (output in the ledger, key never printed).
