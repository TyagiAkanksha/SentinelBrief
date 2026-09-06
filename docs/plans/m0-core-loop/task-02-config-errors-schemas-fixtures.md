---
id: task-02
milestone: m0-core-loop
depends_on: [task-01]
status: planned
spec: PRD.md §1.2, §5 (raw shape), §6.1 (fingerprint), §6.5, §6.6, §10.6; CONVENTIONS.md §4, §7
---

# task-02 — Settings, typed errors, `Verdict` + `SessionAlert` schemas, five fixtures

## Goal

`core/config.py::Settings` constructs with zero env vars and hides secrets; `core/errors.py`
carries the typed family; `core/schemas/verdict.py::Verdict` is PRD §6.5 verbatim in Pydantic v2
style with the §6.6 escalate rule as a validator; `core/schemas/alert.py::SessionAlert` is the
session-level alert envelope with the fingerprint formula from §6.1; five synthetic Cowrie
session fixtures, one per severity band, validate against it.

## Context (read ONLY these)

- `PRD.md` §1.2 (alert unit, `raw` shape), §6.1 step 2 (fingerprint), §6.5 (schema), §6.6
  (rubric: fixtures must *earn* their band), §10.6.
- `CONVENTIONS.md` §4 (errors), §7 (config).
- `.claude/skills/cowrie-fixture/SKILL.md` and its `references/cowrie-events.md` — the verified
  event schema; use `/cowrie-fixture` to author the five files.
- `.env.example` — the documented names and defaults for the M0 settings.

## Files

- Create: `core/config.py`, `core/errors.py`, `core/schemas/verdict.py`, `core/schemas/alert.py`
- Create: `fixtures/alerts/alert1.json` … `alert5.json`, `fixtures/alerts/README.md`
- Create: `tests/test_config.py`, `tests/test_verdict_schema.py`, `tests/test_alert_schema.py`
- Modify: `core/schemas/__init__.py` (re-export `Verdict`, `VerdictCategory`, `SessionAlert`,
  `CowrieEvent`)

## Interfaces

- **Consumes:** the package layout and gates from task-01.
- **Produces (later tasks rely on — produce exactly):**

  ```python
  # core/config.py
  class ModelPrice(BaseModel):
      input_per_mtok: Decimal
      output_per_mtok: Decimal

  class Settings(BaseSettings):                     # model_config: env_file=None (env only), extra="ignore"
      llm_api_key: SecretStr = SecretStr("")
      llm_base_url: str = "https://api.openai.com/v1"
      llm_json_mode: Literal["json_object", "json_schema"] = "json_object"
      cheap_model: str = ""
      strong_model: str = ""
      model_prices_json: dict[str, ModelPrice] = Field(default_factory=dict)   # env MODEL_PRICES_JSON (JSON-decoded)
      triage_prompt_version: str = "triage-v1"
      environment: str = "development"
      @property
      def is_dev(self) -> bool: ...                   # environment.strip().lower() != "production"

  # core/errors.py
  class SentinelBriefError(Exception):
      code: ClassVar[str] = "error"
      def __init__(self, message: str) -> None: ...
  class ConfigError(SentinelBriefError): code = "config_error"
  class LLMCallError(SentinelBriefError): code = "llm_call_failed"
  class StructuredOutputError(SentinelBriefError):
      code = "structured_output"
      def __init__(self, message: str, *, raw_text: str, validation_error: str,
                   input_tokens: int, output_tokens: int, cost_usd: Decimal, latency_ms: int) -> None: ...
  class VerdictValidationError(SentinelBriefError):
      code = "verdict_validation"
      def __init__(self, message: str, *, attempts: int, last_error: str) -> None: ...

  # core/schemas/verdict.py
  VerdictCategory = Literal["scanning", "brute_force", "successful_intrusion",
                            "malware_delivery", "persistence_attempt", "reconnaissance", "other"]
  class Verdict(BaseModel):                          # model_config = ConfigDict(extra="forbid")
      severity: Annotated[int, Field(ge=1, le=5)]
      category: VerdictCategory
      confidence: Annotated[float, Field(ge=0.0, le=1.0)]
      reasoning: Annotated[str, Field(max_length=1200)]
      recommended_action: Annotated[str, Field(max_length=300)]
      escalate: bool
      # @model_validator(mode="after"): severity >= 4 and not escalate -> ValueError("escalate must be true for severity >= 4 (PRD §6.6)")

  # core/schemas/alert.py
  class CowrieEvent(BaseModel):                      # model_config = ConfigDict(extra="allow")
      eventid: str; timestamp: datetime; session: str; src_ip: str; sensor: str; message: str = ""
      username: str | None = None; password: str | None = None; input: str | None = None
      url: str | None = None; outfile: str | None = None; shasum: str | None = None
      duration_ms: int | None = None; version: str | None = None
  class SessionAlert(BaseModel):                     # model_config = ConfigDict(extra="allow")
      source: Literal["cowrie"]; session_id: str; src_ip: str; sensor: str
      events: list[CowrieEvent]                      # Field(min_length=1); after-validator sorts by timestamp
                                                     # and requires events[0].eventid == "cowrie.session.connect"
      @property
      def connect_time(self) -> datetime: ...        # events[0].timestamp
      @property
      def close_time(self) -> datetime | None: ...   # timestamp of the last cowrie.session.closed event, else None
      @property
      def duration_ms(self) -> int | None: ...       # closed event's duration_ms, else int((close-connect)/1ms), else None
      def fingerprint(self) -> str: ...              # sha256("|".join([source, session_id, connect_time.astimezone(UTC).isoformat()])).hexdigest()
  ```

  Fixtures (`fixtures/alerts/`): `alert1.json` connect + closed only (sev 1); `alert2.json` ten
  `login.failed` with common default creds (sev 2); `alert3.json` forty `login.failed` whose
  usernames reference the sensor hostname, no success (sev 3); `alert4.json` `login.success`
  then `uname -a`, `cat /etc/passwd`, `w` (sev 4); `alert5.json` success + `wget http://…/x.sh`
  + `session.file_download` + `crontab -e` (sev 5). `README.md` maps file → scenario → intended
  band (labels proper come in M1).

## Steps (TDD)

- [ ] **Step 1: Write failing tests.** `tests/test_config.py`:
  `test_settings_constructs_with_no_env` (monkeypatch-clear `LLM_*`, `MODEL_PRICES_JSON`;
  `Settings()` succeeds; defaults match `.env.example`), `test_secret_fields_never_in_repr`
  (`"sk-live-123" not in repr(Settings(llm_api_key=SecretStr("sk-live-123")))`),
  `test_model_prices_json_parses_to_decimal` (env
  `MODEL_PRICES_JSON='{"m":{"input_per_mtok":0.15,"output_per_mtok":0.6}}'` →
  `settings.model_prices_json["m"].input_per_mtok == Decimal("0.15")`),
  `test_model_prices_json_invalid_raises` (`MODEL_PRICES_JSON=not-json` → `ValidationError`),
  `test_is_dev_default_true`.
  `tests/test_verdict_schema.py`: `test_valid_verdict_parses`,
  `test_severity_out_of_range_rejected` (0 and 6), `test_unknown_category_rejected`,
  `test_reasoning_over_1200_rejected`, `test_confidence_out_of_range_rejected`,
  `test_severity_ge_4_requires_escalate` (severity 4 + `escalate=False` → `ValidationError`
  whose message contains "PRD §6.6"), `test_extra_fields_rejected`.
  `tests/test_alert_schema.py`: `test_all_five_fixtures_validate` (parametrized over
  `sorted(Path("fixtures/alerts").glob("alert*.json"))`, asserts exactly 5),
  `test_events_must_start_with_session_connect`, `test_events_sorted_by_timestamp` (shuffled
  input comes out ordered), `test_fingerprint_is_sha256_of_source_session_connect_time`
  (recompute with `hashlib` in the test), `test_fingerprint_stable_across_timezone_spellings`
  (`+00:00` vs `Z` vs `+02:00`-shifted equal instants → same hash),
  `test_duration_ms_from_closed_event`, `test_extra_cowrie_fields_survive_model_dump`
  (`hassh` round-trips).
- [ ] **Step 2: Run to see them fail:** `uv run pytest -q tests/test_config.py
  tests/test_verdict_schema.py tests/test_alert_schema.py` → Expected: `ModuleNotFoundError:
  core.config` / `core.schemas.verdict` / `core.schemas.alert` at collection.
- [ ] **Step 3: Implement `core/errors.py`, `core/config.py`** exactly per Interfaces. Docstrings
  cite PRD §6.4/§7.3 for prices and CONVENTIONS §7 for the zero-env rule.
- [ ] **Step 4: Implement `core/schemas/verdict.py`** and export `VERDICT_JSON_SCHEMA =
  Verdict.model_json_schema()` next to it (task-04's prompt embeds it).
- [ ] **Step 5: Implement `core/schemas/alert.py`**; sorting validator uses a stable sort on
  `timestamp`; the fingerprint normalizes to UTC before `isoformat()`.
- [ ] **Step 6: Author the five fixtures with `/cowrie-fixture`**; each is a complete session with
  realistic `message` strings and microsecond timestamps; write `fixtures/alerts/README.md`.
- [ ] **Step 7: Run the three test files → all pass; then `uv run mypy` clean.**
- [ ] **Step 8: Full gates → commit:**
  `feat(core): settings, typed errors, Verdict and SessionAlert schemas, five fixtures (m0 task-02)`.

## Verify

```bash
uv run pytest -q tests/test_config.py tests/test_verdict_schema.py tests/test_alert_schema.py   # 19 passed
uv run python -c "from core.schemas.alert import SessionAlert; import json; a=SessionAlert.model_validate(json.load(open('fixtures/alerts/alert4.json'))); print(a.fingerprint(), a.duration_ms)"
uv run mypy && uv run lint-imports && uv run ruff check .          # clean
```

## Acceptance

- `Settings()` succeeds with no env; secrets never appear in `repr`; prices decode to `Decimal`.
- `Verdict` enforces every §6.5 bound plus the §6.6 escalate rule; extra fields are rejected.
- All five fixtures validate; fingerprints are 64-hex, stable across timezone spellings, and
  change with `session_id`.
