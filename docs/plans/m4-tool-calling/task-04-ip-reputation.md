---
id: task-04
milestone: m4-tool-calling
depends_on: [task-01]
status: planned
spec: PRD.md §6.3 (`lookup_ip_reputation(ip) -> {abuse_score, reports, last_seen}`: AbuseIPDB free tier, 24 h cache, `{unavailable: true}` on quota/miss/no key), §10.6, §13 (AbuseIPDB account is the owner's — ship the unavailable stub until keyed); CONVENTIONS.md §7 (`ABUSEIPDB_API_KEY` is `SecretStr`; bounds are Settings), §10 (mock only the external seam — AbuseIPDB — via `httpx.MockTransport`; `@pytest.mark.live` smoke); `.claude/rules/{worker,tests}.md`; M5 spine task-05 (the cache moves to Redis behind `core.cache.TTLCache`)
---

# task-04 — `lookup_ip_reputation` over AbuseIPDB with a 24 h `TTLCache`, quota/unkeyed/network unavailable paths, recorded reputation fixtures, live smoke

## Goal

`IpReputationTool` calls AbuseIPDB's `check` endpoint through an injected `httpx.AsyncClient` and
answers `{ip, abuse_score, reports, last_seen, cached}`; successful answers are cached for
`ABUSEIPDB_CACHE_TTL_S` (24 h) behind the M3 `core.cache.TTLCache` Protocol — `InMemoryTTLCache`
now, `RedisTTLCache` at M5 in one wiring line — so the free tier's 1 000 checks/day are spent once
per IP per day. Every failure is a typed `{unavailable: true, reason}`: no key (`no_api_key`, logged
once per instance), HTTP 429 (`quota_exceeded`), 401/403 (`unauthorized`), any other non-2xx
(`http_<status>`), a transport error or timeout (`network_error`), a body that is not the
documented shape (`malformed_response`), a malformed address (`invalid_arguments`). Nothing but
a success is ever cached. Unit tests drive the tool through `httpx.MockTransport` — the network
is the only thing faked — and the real endpoint is touched only by an opt-in
`@pytest.mark.live` smoke. Synthetic reputation fixtures for the five fixture IPs land under
`tests/fixtures/tools/lookup_ip_reputation/`.

## Context (read ONLY these)

- `PRD.md` §6.3 (reputation row), §10.6, §13.
- `docs/plans/m4-tool-calling.md` — Global Constraints (24 h cache behind a Protocol; unkeyed →
  unavailable, log once; recorded fixtures, never live APIs in tests).
- `CONVENTIONS.md` §4, §7, §10; `.claude/rules/worker.md`, `.claude/rules/tests.md` (never
  commit a live API response body — fixtures hold the tool's normalized result).
- Task-01 outputs: `worker/tools/base.py`, `worker/tools/recorder.py` (`write_fixture`,
  `ReplayToolRecorder`), `tests/fixtures/tools/README.md`.
- Code you build on: `core/cache.py` (`TTLCache` Protocol — `async get(key) -> bytes | None`,
  `async set(key, value, ttl_s)`; `InMemoryTTLCache(clock=…, max_entries=…)`), `core/config.py`,
  `.env.example` (`ABUSEIPDB_API_KEY` line exists), `tests/test_env_example_roster.py`
  (`_SCHEDULED`), `tests/test_cache.py` (the injectable-clock pattern), `tests/test_llm_client.py`
  (`httpx.MockTransport` handler pattern), `tests/test_triage_live.py` (live-test skip pattern).
- AbuseIPDB API v2 (documented at `docs.abuseipdb.com`, verified at briefing time; the live
  smoke re-verifies): `GET https://api.abuseipdb.com/api/v2/check?ipAddress=<ip>&maxAgeInDays=<n>`
  with headers `Key: <api key>` and `Accept: application/json` → `200 {"data": {"ipAddress",
  "abuseConfidenceScore": int 0–100, "totalReports": int, "lastReportedAt": ISO-8601 string |
  null, …}}`; `429` when the daily quota is exhausted; `401` on a bad key; `422` on a malformed
  address.

## Files

- Create: `worker/tools/ip_reputation.py`,
  `tests/fixtures/tools/lookup_ip_reputation/5d2e7bda8feb939e.json` (203.0.113.10),
  `…/6a624fe81e1c51a3.json` (198.51.100.23), `…/1ba86fc94704aedc.json` (203.0.113.77),
  `…/70c94a209a3ec9bd.json` (192.0.2.55), `…/55230db792e5f6bf.json` (198.51.100.140)
- Create (test-author): `tests/test_ip_reputation_tool.py`, `tests/test_ip_reputation_live.py`
- Modify (test-author, re-pinned): `tests/test_env_example_roster.py` (`ABUSEIPDB_API_KEY`
  leaves `_SCHEDULED`)
- Modify: `core/config.py`, `.env.example`, `worker/tools/__init__.py`

## Interfaces

- **Consumes:** `Tool`, `ToolContext`, `unavailable` (`worker.tools.base`); `write_fixture`,
  `ReplayToolRecorder` (`worker.tools.recorder`); `TTLCache`, `InMemoryTTLCache` (`core.cache`);
  `Settings`; `httpx.AsyncClient`, `httpx.MockTransport`.
- **Produces (task-06 wiring and M5 task-05 rely on — produce exactly):**

  ```python
  # worker/tools/ip_reputation.py
  ABUSEIPDB_CHECK_URL = "https://api.abuseipdb.com/api/v2/check"    # a vendor endpoint, not a threshold/secret: a module constant, not a Setting
  CACHE_KEY_PREFIX = "abuseipdb:"                                      # cache key = CACHE_KEY_PREFIX + ip (M5's Redis keyspace is shared with the API cache)
  class IpReputationTool:
      name = "lookup_ip_reputation"
      external = True
      description = "Look up an IP address's abuse reputation (AbuseIPDB confidence score 0-100, report count, last report time). Costs quota; call it once per address."
      parameters = {"type": "object", "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."}},
                    "required": ["ip"], "additionalProperties": False}
      def __init__(self, *, api_key: str, http: httpx.AsyncClient, cache: TTLCache, cache_ttl_s: int, max_age_days: int) -> None: ...
          # cache_ttl_s < 1 or max_age_days < 1 -> ValueError; `http` carries the timeout (built by task-06's wiring from ABUSEIPDB_TIMEOUT_S)
      async def run(self, arguments, ctx) -> dict[str, Any]: ...
          # ip missing / not str / ipaddress.ip_address raises -> unavailable("invalid_arguments")     (checked before the key: never spend quota on garbage)
          # api_key == ""                                        -> unavailable("no_api_key"), logger.warning once per instance
          # cached = await cache.get(CACHE_KEY_PREFIX + ip); hit -> json.loads(cached) | {"cached": True}
          # GET ABUSEIPDB_CHECK_URL, params {"ipAddress": ip, "maxAgeInDays": str(max_age_days)}, headers {"Key": api_key, "Accept": "application/json"}
          #   httpx.HTTPError (timeout, connect, read)  -> unavailable("network_error")
          #   429 -> unavailable("quota_exceeded"); 401 | 403 -> unavailable("unauthorized"); other non-2xx -> unavailable(f"http_{status}")
          #   body: data = body["data"]; result = {"ip": ip, "abuse_score": int(data["abuseConfidenceScore"]), "reports": int(data["totalReports"]),
          #         "last_seen": data.get("lastReportedAt")  (str | None)}
          #     json / KeyError / TypeError / ValueError -> unavailable("malformed_response")
          #   await cache.set(key, json.dumps(result).encode(), cache_ttl_s)     # SUCCESS ONLY — an unavailable answer is never cached
          #   return result | {"cached": False}
          # Never raises. The key is sent in the `Key` header only and never appears in a result, a log line, or an exception message
          # that this module produces (httpx errors are mapped to the reason string, their text is not echoed).

  # core/config.py — new fields (+ .env.example lines under "Enrichment tools (from M4)")
  abuseipdb_api_key: SecretStr = SecretStr("")                        # ABUSEIPDB_API_KEY=          (line exists; graduates; SECRET, optional)
  abuseipdb_cache_ttl_s: Annotated[int, Field(ge=1)] = 86400          # ABUSEIPDB_CACHE_TTL_S=86400 (PRD §6.3: 24 h)
  abuseipdb_timeout_s: Annotated[float, Field(gt=0)] = 5.0            # ABUSEIPDB_TIMEOUT_S=5       (per request; the loop must not hang on a slow vendor)
  abuseipdb_max_age_days: Annotated[int, Field(ge=1, le=365)] = 90    # ABUSEIPDB_MAX_AGE_DAYS=90   (AbuseIPDB's own default window)
  abuseipdb_cache_max_entries: Annotated[int, Field(ge=1)] = 4096     # ABUSEIPDB_CACHE_MAX_ENTRIES=4096 (bound on the in-process cache; Redis at M5 uses maxmemory)

  # tests/fixtures/tools/lookup_ip_reputation/<key>.json — synthetic normalized results (never a provider body), per fixtures/alerts:
  #   203.0.113.10   -> {"ip": "203.0.113.10",   "abuse_score": 100, "reports": 412,  "last_seen": "2026-09-05T22:14:03+00:00"}
  #   198.51.100.23  -> {"ip": "198.51.100.23",  "abuse_score": 87,  "reports": 96,   "last_seen": "2026-09-04T03:41:19+00:00"}
  #   203.0.113.77   -> {"ip": "203.0.113.77",   "abuse_score": 23,  "reports": 4,    "last_seen": "2026-08-30T11:07:55+00:00"}
  #   192.0.2.55     -> {"ip": "192.0.2.55",     "abuse_score": 64,  "reports": 31,   "last_seen": "2026-09-06T01:12:40+00:00"}
  #   198.51.100.140 -> {"ip": "198.51.100.140", "abuse_score": 100, "reports": 1209, "last_seen": "2026-09-06T20:58:02+00:00"}
  #   (no "cached" key in a fixture: replay results are what the tool computed, and the loop persists them verbatim)
  ```

## Interfaces → test table

Every unit test builds the tool over `httpx.AsyncClient(transport=httpx.MockTransport(handler))`
and `InMemoryTTLCache(clock=fake_clock)`; `handler` records requests and returns the scripted
response. The API key in tests is the literal `"test-key"`.

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| request shape | `tests/test_ip_reputation_tool.py::test_sends_key_header_accept_json_and_query_params` | one request to `ABUSEIPDB_CHECK_URL`, `headers["Key"] == "test-key"`, `headers["Accept"] == "application/json"`, `params == {"ipAddress": "203.0.113.10", "maxAgeInDays": "90"}`; fails when the key travels as a query param or `maxAgeInDays` is dropped |
| success mapping | `tests/test_ip_reputation_tool.py::test_maps_data_fields_to_abuse_score_reports_last_seen` | `{"data": {"abuseConfidenceScore": 87, "totalReports": 96, "lastReportedAt": "2026-09-04T03:41:19+00:00"}}` → `{"ip", "abuse_score": 87, "reports": 96, "last_seen": …, "cached": False}`; `lastReportedAt: null` → `last_seen is None` |
| cache hit within TTL | `tests/test_ip_reputation_tool.py::test_second_lookup_within_ttl_is_served_from_cache` | two runs → one HTTP request; second result equals the first except `cached is True`; fails when the request count is 2 |
| cache expiry | `tests/test_ip_reputation_tool.py::test_lookup_after_ttl_hits_the_network_again` | clock advanced by `cache_ttl_s` → second request made; fails when `ttl_s` passed to `set` differs from `cache_ttl_s` (assert via a recording `TTLCache` stub) |
| cache key | `tests/test_ip_reputation_tool.py::test_cache_key_is_prefixed_with_the_ip` | recording cache saw `set("abuseipdb:203.0.113.10", …)` |
| unavailable never cached | `tests/test_ip_reputation_tool.py::test_failures_are_never_cached` | a 429 then a 200 → both requests made, second succeeds; recording cache saw zero `set` calls after the 429; fails when the unavailable answer is cached |
| no key, logs once | `tests/test_ip_reputation_tool.py::test_no_api_key_is_unavailable_and_logs_once_without_network` | `api_key=""` → `unavailable("no_api_key")` twice, zero requests, exactly one WARNING in `caplog` |
| quota | `tests/test_ip_reputation_tool.py::test_429_is_quota_exceeded` | `unavailable("quota_exceeded")` |
| unauthorized | `tests/test_ip_reputation_tool.py::test_401_and_403_are_unauthorized` | both → `unavailable("unauthorized")` |
| other status | `tests/test_ip_reputation_tool.py::test_other_non_2xx_is_http_status` | 500 → `unavailable("http_500")`; 422 → `unavailable("http_422")` |
| network / timeout | `tests/test_ip_reputation_tool.py::test_transport_error_and_timeout_are_network_error` | handler raising `httpx.ConnectError` and `httpx.ReadTimeout` → `unavailable("network_error")`; never propagates |
| malformed body | `tests/test_ip_reputation_tool.py::test_malformed_bodies_are_malformed_response` | `not json`, `{}`, `{"data": {"abuseConfidenceScore": "high"}}` → `unavailable("malformed_response")` |
| invalid ip before key | `tests/test_ip_reputation_tool.py::test_invalid_ip_is_invalid_arguments_before_any_request` | `"nope"`, missing, `1` → `invalid_arguments`; zero requests even with a key |
| key never leaks | `tests/test_ip_reputation_tool.py::test_key_never_appears_in_results_or_logs` | across a success, a 429 and a `ConnectError("…Key test-key…")`, `"test-key"` is in no result value and not in `caplog.text` |
| constructor bounds | `tests/test_ip_reputation_tool.py::test_rejects_nonpositive_ttl_and_max_age` | `cache_ttl_s=0`, `max_age_days=0` → `ValueError` |
| `external` | `tests/test_ip_reputation_tool.py::test_tool_is_external` | `external is True` |
| settings | `tests/test_ip_reputation_tool.py::test_abuseipdb_settings_defaults_bounds_and_secret_repr` | defaults `86400 / 5.0 / 90 / 4096`; `abuseipdb_max_age_days=366` and `abuseipdb_timeout_s=0` → `ValidationError`; `repr(Settings(abuseipdb_api_key=SecretStr("k")))` has no `k`… use a distinctive value `"abuse-key-1"`; roster green after graduation |
| fixtures replay | `tests/test_ip_reputation_tool.py::test_recorded_fixtures_replay_for_the_five_fixture_ips` | `ReplayToolRecorder(Path("tests/fixtures/tools"))` → five results with the scores above; file names equal `fixture_key(arguments)`; none has a `cached` key |
| live smoke | `tests/test_ip_reputation_live.py::test_live_check_returns_a_score_in_range` (`@pytest.mark.live`) | skips unless `ABUSEIPDB_API_KEY` is set; real `httpx.AsyncClient(timeout=settings.abuseipdb_timeout_s)`, `InMemoryTTLCache()`, ip `1.1.1.1` (a public resolver — shape only is asserted) → `0 <= abuse_score <= 100`, `reports >= 0`; never prints the key |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the two new files plus the re-pinned
roster test; the **implementer** does Steps 3–5 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the two test files** per the table (a `RecordingCache`
  implementing `TTLCache` that logs `set` calls and delegates to `InMemoryTTLCache` is defined in
  the test file — it implements our Protocol, it is not a mock of our code); remove
  `"ABUSEIPDB_API_KEY"` from `_SCHEDULED`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q
  tests/test_ip_reputation_tool.py` → Expected: `ModuleNotFoundError: No module named
  'worker.tools.ip_reputation'`; roster test names `ABUSEIPDB_API_KEY`; `uv run pytest -q -m live
  tests/test_ip_reputation_live.py` → skipped `ABUSEIPDB_API_KEY not set` (record it). Pin,
  commit `test(worker): lookup_ip_reputation tool RED (m4 task-04)`.
- [ ] **Step 3 (GREEN — implementer): `core/config.py` fields + `.env.example` lines**; roster green.
- [ ] **Step 4 (GREEN — implementer): `worker/tools/ip_reputation.py` + re-export; the five fixture
  files via `write_fixture`** (paste the loop and the `ls` into the report). `uv run mypy` clean.
- [ ] **Step 5 (implementer): all tests in the table + the existing suite green; full gates →
  commit:** `feat(worker): lookup_ip_reputation over AbuseIPDB with 24 h TTLCache, reputation fixtures (m4 task-04)`
  with the two trailers; path-scoped `git add worker/tools core/config.py .env.example
  tests/fixtures/tools/lookup_ip_reputation`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_ip_reputation_tool.py tests/test_env_example_roster.py tests/test_cache.py   # every test in the table passes
uv run pytest -q -m live tests/test_ip_reputation_live.py                                              # skipped without ABUSEIPDB_API_KEY; passes with it
ls tests/fixtures/tools/lookup_ip_reputation | wc -l                                                    # 5
grep -rn "abuseipdb.com" worker/ | grep -v "worker/tools/ip_reputation.py" ; echo "exit=$?"            # exit=1 — one module talks to the vendor
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean
```

## Acceptance

- `lookup_ip_reputation` returns `{ip, abuse_score, reports, last_seen, cached}` from AbuseIPDB
  with the key in the `Key` header only, serves repeats from the `TTLCache` for
  `ABUSEIPDB_CACHE_TTL_S`, never caches a failure, and maps no-key / 429 / 401–403 / other
  non-2xx / transport errors / malformed bodies / bad addresses to the eight documented
  `unavailable` reasons without ever raising or leaking the key.
- All numeric bounds are `Settings` with `.env.example` lines; the live smoke is opt-in and
  skipped by default; five synthetic fixtures replay for the fixture alerts' IPs.
