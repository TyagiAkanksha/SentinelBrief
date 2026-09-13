---
id: task-01
milestone: m8-polish
depends_on: [m5 tag]
status: planned
spec: PRD.md §8 (`GET /api/v1/stream` — "SSE: `verdict.created` events (id, severity, category, summary line). Fed by Redis pub/sub"), §9 page 1 ("Live-updates via SSE (falls back to 30 s polling)"), §10.1 (no public request path may trigger an LLM call), §10.6 (attacker-controlled strings are data, never instructions); `docs/FRONTEND-CONVENTIONS.md` §3, §6, §7; `.claude/rules/{api,web,tests}.md`; CONVENTIONS.md §4, §5, §8
---

# task-01 — `GET /api/v1/stream`: Server-Sent Events fed by the `verdict.created` Redis channel, plus `useAlertStream` with a 30 s polling fallback wired into the queue page

## Goal

The queue page updates itself. `api/routes/stream.py` subscribes to the Redis pub/sub channel
`sentinelbrief:verdict.created` (published since m5 task-02 by `worker/publish.py`) and streams
each message to the browser as a named SSE event, with periodic comment heartbeats so proxies and
load balancers keep the connection open. `web/src/hooks/useAlertStream.ts` consumes it through the
browser's `EventSource`, and on every event calls `router.refresh()` so the React Server Component
re-reads the database — **the SSE payload is a hint, never the rendered data** (m5 task-04 review
M4). When the stream errors, the hook falls back to calling the same refresh on a 30 s interval and
says so on screen; when the stream comes back, it returns to live.

Two properties carry the security weight of this task:

1. **The SSE frame cannot be forged from alert content.** `summary` is model text derived from
   attacker-influenced session data (PRD §10.6). A newline in it would end the `data:` line and let
   an attacker inject a whole synthetic event. Every event body is rendered by serializing a
   validated Pydantic model to JSON, which escapes `\n`, `\r` and ` `, so one message is always
   exactly one physical `data:` line — pinned by a test that feeds a payload containing
   `"\n\nevent: verdict.created\ndata: {\"severity\": 1}"`.
2. **A public, unauthenticated, long-lived endpoint is a bounded resource.** Each client holds one
   HTTP connection and one Redis pub/sub connection, so concurrent streams are capped by
   `STREAM_MAX_CLIENTS` and the over-limit request gets the standard `429` envelope rather than
   another connection.

## Context (read ONLY these)

- `PRD.md` §8 (the `/stream` row and the "one envelope everywhere" paragraph), §9 (page 1), §10.1,
  §10.6.
- `docs/plans/m8-polish.md` — Goal, **Global Constraints** (all of them, including the M8a section).
- `docs/FRONTEND-CONVENTIONS.md` §3 (dumb components, logic in hooks), §5 (one codegen boundary),
  §6 (`NEXT_PUBLIC_*` is inlined at build; only the SSE hook runs in the browser), §7 (Vitest,
  jsdom per file, mock only the network seam), §9 (accessibility).
- `CONVENTIONS.md` §4 (typed errors, no try/except in routes), §5 (`create_app`, `app.state`,
  `operation_id`), §8 (the OpenAPI baseline and web codegen move in the same commit), §9, §10.
- `.claude/rules/api.md`, `.claude/rules/web.md`, `.claude/rules/tests.md`.
- Code you build on — read these files, not the rest of the tree:
  - `core/queue.py` (`VERDICT_CREATED_CHANNEL = "sentinelbrief:verdict.created"`, `make_redis`)
  - `worker/publish.py` (`verdict_created_payload` — the exact six keys this task parses)
  - `core/schemas/alerts_read.py` (`reasoning_excerpt`, `REASONING_EXCERPT_CHARS = 160`)
  - `core/schemas/verdict.py` (`VerdictCategory`)
  - `core/errors.py` (the typed family and their `code` values), `api/errors.py`
    (`STATUS_BY_ERROR`, `status_for`)
  - `api/factory.py` (`create_app`, `app.state`, the CORS middleware, `API_V1_PREFIX`)
  - `api/deps.py` (`get_settings`, `SessionDep` — the shape a new dependency must follow)
  - `api/routes/health.py` (how `app.state.redis` is probed), `api/routes/alerts_read.py` (route
    style, `operation_id`, `responses={...: {"model": ErrorEnvelope}}`)
  - `core/config.py` (`Settings`, `redis_socket_timeout_s`), `.env.example`
  - `tests/conftest.py` (`arq_redis`, `redis_url`, `settings` fixtures), `tests/test_publish.py`,
    `tests/test_health.py`, `tests/test_cors.py`, `tests/test_error_status_mapping.py`,
    `tests/test_openapi_hygiene.py`, `tests/test_env_example_roster.py`
  - `web/src/lib/api/server.ts` (the `apiUrl()` shape `publicApiUrl()` mirrors),
    `web/src/app/alerts/page.tsx`, `web/src/components/alerts/AlertQueue/`,
    `web/src/components/ui/Badge/` (a dumb primitive's folder shape), `web/vitest.config.ts`

## Files

- Create: `core/schemas/stream.py`, `api/routes/stream.py`
- Modify: `core/errors.py` (`StreamUnavailableError`), `api/errors.py` (`STATUS_BY_ERROR` row),
  `api/deps.py` (`get_redis` / `RedisDep`), `api/factory.py` (`app.state.stream_gate`, include the
  router), `core/config.py` (`stream_heartbeat_s`, `stream_max_clients`), `.env.example`,
  `api/openapi.json` (regenerated), `web/src/types/generated/schema.d.ts` (regenerated)
- Create (web): `web/src/lib/api/browser.ts`, `web/src/hooks/useAlertStream.ts`,
  `web/src/components/alerts/LiveIndicator/{LiveIndicator.tsx,interface.ts,index.ts}`,
  `web/src/components/alerts/AlertStreamRefresher/{AlertStreamRefresher.tsx,interface.ts,index.ts}`
- Modify (web): `web/src/app/alerts/page.tsx`
- Create (test-author — these are the pinned files):
  `tests/test_stream_events.py`, `tests/test_stream_route.py`,
  `web/src/lib/api/browser.test.ts`, `web/src/hooks/useAlertStream.test.ts`,
  `web/src/components/alerts/LiveIndicator/LiveIndicator.test.tsx`,
  `web/src/components/alerts/AlertStreamRefresher/AlertStreamRefresher.test.tsx`
- Modify (test-author): `tests/test_error_status_mapping.py` and `tests/test_env_example_roster.py`
  **only if** they enumerate an exact set that the new error class / the two new settings would
  break. Check first; if no change is needed, say so in the report.

## Interfaces

- **Consumes (exists today — do not redefine):**

  ```python
  # core/queue.py
  VERDICT_CREATED_CHANNEL = "sentinelbrief:verdict.created"
  # worker/publish.py::verdict_created_payload -> the published JSON object, exactly these keys:
  #   {"alert_id": str(uuid), "verdict_id": str(uuid), "severity": int, "category": str,
  #    "escalate": bool, "summary": str}   # summary = reasoning_excerpt(verdict.reasoning), <= 160 chars
  # api/factory.py: app.state.redis is the ArqRedis client (or None when unwired)
  # core/config.py: Settings.redis_socket_timeout_s (default 2.0) bounds every socket read
  ```

- **Produces (exactly):**

  ```python
  # core/schemas/stream.py
  class VerdictCreatedEvent(BaseModel):
      """One `verdict.created` message, validated on the way OUT of Redis and back to JSON."""
      model_config = ConfigDict(extra="forbid")
      alert_id: uuid.UUID
      verdict_id: uuid.UUID
      severity: Annotated[int, Field(ge=1, le=5)]
      category: VerdictCategory
      escalate: bool
      summary: Annotated[str, Field(max_length=REASONING_EXCERPT_CHARS)]

  # core/errors.py  (append, alphabetical position not required — keep the file's existing order)
  class StreamUnavailableError(SentinelBriefError):
      """The event stream's Redis seam is not wired or not reachable."""
      code = "stream_unavailable"
  # api/errors.py: STATUS_BY_ERROR gains  StreamUnavailableError: 503

  # core/config.py — two new Settings fields, each with its .env.example line
  stream_heartbeat_s: Annotated[float, Field(gt=0)] = 15.0
  stream_max_clients: Annotated[int, Field(ge=1)] = 50

  # api/deps.py
  def get_redis(request: Request) -> Redis: ...
      # app.state.redis, or raise StreamUnavailableError("event stream unavailable") when None.
      # NOT RuntimeError: an unwired stream is a 503 the dashboard can render, not a 500.
  RedisDep = Annotated[Redis, Depends(get_redis)]

  # api/routes/stream.py
  router = APIRouter()
  HEARTBEAT_COMMENT = "heartbeat"
  VERDICT_CREATED_EVENT = "verdict.created"          # the SSE `event:` name the hook listens for
  SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}

  def format_sse(event: str, data: str) -> str: ...      # f"event: {event}\ndata: {data}\n\n"
  def format_comment(text: str) -> str: ...              # f": {text}\n\n"   (invisible to EventSource listeners; keeps the connection warm)
  def render_verdict_event(raw: str | bytes) -> str | None: ...
      # json.loads -> VerdictCreatedEvent.model_validate -> format_sse(VERDICT_CREATED_EVENT, event.model_dump_json())
      # Returns None (caller logs a WARNING naming the exception CLASS only, never the payload) on
      # json.JSONDecodeError, UnicodeDecodeError, or pydantic.ValidationError. This is the only
      # place a channel message is turned into wire bytes: model_dump_json() is what escapes the
      # newlines in `summary`, so one message is always exactly one `data:` line (PRD §10.6).

  @dataclass
  class StreamGate:
      """Bounds concurrent SSE clients: one HTTP + one Redis pub/sub connection each."""
      limit: int
      active: int = 0
      def acquire(self) -> bool: ...   # False when active >= limit, else active += 1 and True
      def release(self) -> None: ...   # max(0, active - 1)
      # PAIRING CONTRACT (say this in both docstrings): the ROUTE acquires; the GENERATOR releases
      # in its `finally`. Starlette always closes a StreamingResponse's generator — on normal end,
      # on client disconnect, and on shutdown — so every acquire has exactly one release.

  async def verdict_event_stream(redis: Redis, *, heartbeat_s: float, gate: StreamGate | None = None) -> AsyncIterator[str]: ...
      # pubsub = redis.pubsub(); await pubsub.subscribe(VERDICT_CREATED_CHANNEL)
      # loop: message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=heartbeat_s)
      #   message is None                      -> yield format_comment(HEARTBEAT_COMMENT)
      #   RedisTimeoutError                    -> yield format_comment(HEARTBEAT_COMMENT) and CONTINUE.
      #       NOTE (ruling R14): the parameter is named `redis`, which SHADOWS the top-level
      #       `redis` package inside this function — `except redis.exceptions.TimeoutError`
      #       raises AttributeError at runtime. Import the classes at module level instead:
      #       `from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError`.
      #       Why: the shared client's socket_timeout (Settings.redis_socket_timeout_s, 2.0) can fire
      #       before `timeout=heartbeat_s` does, and a quiet channel must never end the stream. The
      #       effective heartbeat is therefore min(heartbeat_s, redis_socket_timeout_s) — harmless,
      #       and stated in the docstring (briefing ruling R1).
      #   other RedisError | OSError           -> logger.warning("... exc=%s", type(exc).__name__); return
      #       (the browser's EventSource reconnects on its own; the hook falls back to polling meanwhile)
      #   a message                            -> rendered = render_verdict_event(message["data"]);
      #                                           yield it when not None, else log + skip
      # Cleanup (ruling R15): the gate release must be in an OUTER `finally` that the pubsub
      # cleanup cannot skip — a raising `unsubscribe()`/`aclose()` during a disconnect-driven
      # cancellation would otherwise leak a permit and eventually 429 every client:
      #   try:
      #       try: ...the loop...
      #       finally: await pubsub.unsubscribe(); await pubsub.aclose()   # may raise
      #   finally:
      #       if gate is not None: gate.release()

  @router.get("/stream", operation_id="stream_verdicts", response_class=StreamingResponse,
              responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream of verdict.created events."},
                         429: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}})
  async def stream_verdicts(request: Request, redis: RedisDep, settings: Settings = Depends(get_settings)) -> StreamingResponse: ...
      # gate = request.app.state.stream_gate
      # if not gate.acquire(): raise RateLimitedError("too many concurrent stream clients")   # 429 envelope
      # return StreamingResponse(verdict_event_stream(redis, heartbeat_s=settings.stream_heartbeat_s, gate=gate),
      #                          media_type="text/event-stream", headers=SSE_HEADERS)
      # No try/except in the route (.claude/rules/api.md): the gate is released by the generator.

  # api/factory.py — inside create_app(), next to the existing app.state assignments:
  app.state.stream_gate = StreamGate(limit=effective_settings.stream_max_clients)
  app.include_router(stream_router, prefix=API_V1_PREFIX)
  ```

  ```ts
  // web/src/lib/api/browser.ts — the ONLY browser-side origin reader (FRONTEND-CONVENTIONS §6)
  export const DEFAULT_DEV_PUBLIC_API_URL = "http://localhost:8000";
  export function publicApiUrl(): string;
    // process.env.NEXT_PUBLIC_API_URL must be referenced as that exact literal expression (Next
    // inlines it at build). Trailing slashes stripped. Unset + NODE_ENV === "production" ->
    // throw new Error("NEXT_PUBLIC_API_URL is not set"); unset otherwise -> DEFAULT_DEV_PUBLIC_API_URL.
  export function streamUrl(): string;         // `${publicApiUrl()}/api/v1/stream`

  // web/src/hooks/useAlertStream.ts
  export const VERDICT_CREATED_EVENT = "verdict.created";
  export const POLL_INTERVAL_MS = 30_000;                       // PRD §9: "falls back to 30 s polling"
  export type StreamStatus = "connecting" | "live" | "polling";
  export type EventSourceLike = {
    addEventListener(type: string, listener: (event: Event) => void): void;
    close(): void;
  };
  export type UseAlertStreamOptions = {
    url: string;
    onUpdate: () => void;                                       // called on every event AND every poll tick
    pollIntervalMs?: number;                                    // default POLL_INTERVAL_MS
    createEventSource?: (url: string) => EventSourceLike;       // default (u) => new EventSource(u) — the ONE network seam tests replace
  };
  export type AlertStreamState = { status: StreamStatus; updates: number };
  export function useAlertStream(options: UseAlertStreamOptions): AlertStreamState;
    // One EventSource per `url` (a useEffect keyed on url and pollIntervalMs ONLY). `onUpdate` and
    // `createEventSource` are held in refs and kept current, so a new callback identity on re-render
    // never tears down and re-opens the stream.
    // "open"            -> status "live";    clear the poll timer
    // VERDICT_CREATED_EVENT -> updates + 1;  onUpdate()      — event.data is NEVER read, parsed or
    //                        rendered: the payload is a hint and the page re-reads the database
    //                        (m5 task-04 review M4, and it keeps attacker-influenced text off the
    //                        client render path entirely)
    // "error"           -> status "polling"; start setInterval(onUpdate, pollIntervalMs) if not running
    // cleanup           -> close() the source and clear the timer

  // web/src/components/alerts/LiveIndicator/interface.ts
  export type LiveIndicatorProps = { status: StreamStatus; updates: number };
  // LiveIndicator.tsx: a dumb <p role="status" aria-live="polite"> whose TEXT carries the state
  //   "connecting" -> "Connecting…"   "live" -> "Live"   "polling" -> "Polling every 30s"
  //   plus ` · ${updates} update(s)` when updates > 0. Colour comes from a token class
  //   (text-sev-2 live / text-muted connecting / text-sev-3 polling) and is never the only signal.

  // web/src/components/alerts/AlertStreamRefresher/AlertStreamRefresher.tsx — "use client"
  //   const router = useRouter();
  //   const onUpdate = useCallback(() => { router.refresh(); }, [router]);
  //   const state = useAlertStream({ url: streamUrl(), onUpdate });
  //   return <LiveIndicator status={state.status} updates={state.updates} />;
  // interface.ts exports `export type AlertStreamRefresherProps = Record<string, never>;` (no props).

  // web/src/app/alerts/page.tsx — render <AlertStreamRefresher /> directly under the <h1>, above
  // <FilterBar />. The page stays a Server Component; only the refresher is a client island.
  ```

## Briefing rulings (decided — do not re-litigate; raise only if implementation proves one wrong)

- **R1 — a read timeout is a heartbeat, not an end of stream.** The stream reuses the single
  `app.state.redis` client rather than opening a second one with its own socket timeout. Its 2 s
  socket timeout can surface as `redis.exceptions.TimeoutError` from `get_message`; that is caught
  and yields a heartbeat. Cost if wrong: heartbeats arrive more often than `STREAM_HEARTBEAT_S`.
- **R2 — the payload is a hint.** The hook never reads `event.data`; the page re-reads the database
  through `router.refresh()`. This is the m5 task-04 review M4 deferral, and it also means no
  attacker-influenced string from the channel is ever rendered from the stream path.
- **R3 — the gate lives on `app.state`, not in a module global.** Two apps in one test process must
  not share a counter. `create_app()` installs it; the route acquires; the generator releases.
- **R12 — the route's live test runs against a real `uvicorn.Server`, not `httpx.ASGITransport`.**
  Proven empirically by the task-01 test-author (traced into httpx 0.28.1; reproduced a 120 s hang):
  `ASGITransport.handle_async_request` collects every `http.response.body` chunk inside one `await`
  of the app coroutine, so an endless `StreamingResponse` never yields a partial body to the client.
  A real server on a real socket is also closer to production, including Starlette's client-disconnect
  handling. Cost if wrong: a slower, socket-bound test.
- **R13 — the frame-injection test asserts on physical lines, not substring counts.** The brief's
  original `frame.count("event:") == 1` is unsatisfiable for its own example payload: JSON-escaping
  leaves the literal words `event:` intact inside the `summary` value and escapes only the control
  characters, so the substring appears twice however correct the implementation is. Cost if wrong:
  nothing — the line-based form pins the property the substring form was reaching for.
- **R14 — import the Redis exception classes at module level.** `verdict_event_stream`'s parameter
  is named `redis`, which shadows the package inside the function body; `except
  redis.exceptions.TimeoutError` there raises `AttributeError`. Use
  `from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError`.
- **R15 — the gate release lives in an outer `finally` the pubsub cleanup cannot skip.** A raising
  `unsubscribe()`/`aclose()` during a disconnect-driven cancellation would otherwise leak a permit
  and, after `STREAM_MAX_CLIENTS` such failures, 429 every client forever.
- **R4 — no auto-reconnect logic of our own.** `EventSource` reconnects natively; the hook's job is
  to notice (`error`) and keep the page fresh by polling until `open` returns. Cost if wrong: a
  page that was offline for a while refreshes on the 30 s tick rather than instantly.

## Interfaces → test table

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| `format_sse` / `format_comment` | `tests/test_stream_events.py::test_format_sse_and_comment_frames` | exact bytes: `"event: verdict.created\ndata: {...}\n\n"`, `": heartbeat\n\n"` |
| `render_verdict_event` happy path | `::test_render_verdict_event_round_trips_a_published_payload` | builds the input with `worker.publish.verdict_created_payload` (never a hand-written dict), asserts the rendered frame parses back to the same six fields |
| **frame injection (load-bearing)** | `::test_render_verdict_event_cannot_be_escaped_by_a_newline_in_summary` | a payload whose `summary` is `"a\n\nevent: verdict.created\ndata: {\"severity\": 1}"` (and one with `\r`) renders to **exactly one** physical `data:` line. Assert on LINES, not substrings (ruling R13): split the frame on `\n`, then exactly one line starts with `event:` and exactly one with `data:`, `frame.count("\n") == 3`, and the frame ends with exactly one `\n\n`. A raw `frame.count("event:")` is 2 for this payload however secure the implementation is — the words survive JSON-escaping inside the string value; only the control characters around them are escaped |
| `render_verdict_event` rejects | `::test_render_verdict_event_returns_none_for_bad_json_bad_schema_and_bad_bytes` | `b"{"`, a payload with `severity: 9`, one with an unknown extra key, `b"\xff"` → `None` each |
| `StreamGate` | `::test_stream_gate_acquires_up_to_the_limit_and_releases` | limit 2: two acquires True, third False; release then acquire True; release below zero stays 0 |
| heartbeat on idle + on `TimeoutError` | `::test_verdict_event_stream_yields_heartbeats_on_idle_and_on_socket_timeout` | fake pubsub returning `None`, then raising `redis.exceptions.TimeoutError`, then a real message → two heartbeats then one event |
| fatal Redis error ends the stream | `::test_verdict_event_stream_ends_on_connection_error_and_logs_the_class_only` | `caplog` at WARNING contains `ConnectionError` and does NOT contain the message text or any URL |
| gate release on every exit | `::test_verdict_event_stream_releases_the_gate_on_normal_end_and_on_close` | generator exhausted → `active == 0`; generator `aclose()`d mid-stream → `active == 0` |
| route 200 + headers + live event | `tests/test_stream_route.py::test_stream_delivers_a_published_verdict_event` (DB-less app, `arq_redis` fixture, a real `uvicorn.Server` on a free `127.0.0.1` port — ruling R12) | status 200, `content-type` starts `text/event-stream`, `cache-control` contains `no-cache`; poll `await arq_redis.pubsub_numsub(VERDICT_CREATED_CHANNEL)` until the subscriber count is 1, then `publish_verdict_created(...)`, then read lines until `event: verdict.created` with `asyncio.wait_for(..., 5)` |
| unwired Redis | `::test_stream_503_envelope_when_redis_is_unwired` | `create_app()` with no `redis=` → 503, body `{"error":{"code":"stream_unavailable","message":...}}` |
| over the client cap | `::test_stream_429_envelope_when_the_client_cap_is_reached` | `stream_max_clients=1`: the first stream open, the second GET → 429 with `code == "rate_limited"`; after the first closes, a new GET is 200 again |
| CORS for the browser | `::test_stream_sends_access_control_allow_origin_for_a_configured_origin` | `Origin: http://localhost:3000` → header echoed (EventSource is cross-origin in every deployed environment) |
| OpenAPI surface | `::test_openapi_declares_the_stream_operation_with_event_stream_content` | `create_app().openapi()`: `operationId == "stream_verdicts"`, 200 content key `text/event-stream`, 429 and 503 ref `ErrorEnvelope` (the existing `tests/test_openapi_hygiene.py` enforces the envelope rule too) |
| no LLM on this path | `::test_stream_module_imports_no_worker_or_llm` | `api.routes.stream`'s source contains no `worker`/`core.llm` import and `lint-imports` stays green (PRD §10.1) |
| settings roster | existing `tests/test_env_example_roster.py` | `STREAM_HEARTBEAT_S` and `STREAM_MAX_CLIENTS` lines present |
| `publicApiUrl` | `web/src/lib/api/browser.test.ts` — `it("publicApiUrl strips trailing slashes")` | `vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example/")` → `"https://api.example"` |
| `publicApiUrl` production unset | `it("publicApiUrl throws when NEXT_PUBLIC_API_URL is unset under NODE_ENV=production")` | `toThrow("NEXT_PUBLIC_API_URL is not set")` |
| `publicApiUrl` dev fallback + `streamUrl` | `it("publicApiUrl falls back to localhost and streamUrl appends the stream path")` | `DEFAULT_DEV_PUBLIC_API_URL`; `streamUrl()` ends `/api/v1/stream` |
| hook: live on open | `web/src/hooks/useAlertStream.test.ts` — `it("reports live once the stream opens")` | fake source; `status === "live"` |
| hook: event → onUpdate, data ignored | `it("calls onUpdate on a verdict.created event and never reads event.data")` | dispatch an event whose `data` is a getter that fails the test if read (or a `MessageEvent` whose data is `"\nevent: forged"`); `onUpdate` called once, `updates === 1`, nothing rendered from data |
| hook: polling fallback | `it("falls back to 30 s polling after an error and keeps refreshing")` | `vi.useFakeTimers()`; emit `error` → `status === "polling"`; advance 30 000 ms twice → `onUpdate` called twice more |
| hook: recovery | `it("returns to live and stops polling when the stream reopens")` | error → open: `status === "live"`; advancing 60 000 ms adds no further `onUpdate` calls |
| hook: stable subscription | `it("does not reopen the stream when onUpdate changes identity")` | rerender with a new callback → `createEventSource` called exactly once; the new callback is the one invoked |
| hook: cleanup | `it("closes the stream and clears the timer on unmount")` | `close` called once; no `onUpdate` after unmount across 60 000 ms |
| indicator text | `web/src/components/alerts/LiveIndicator/LiveIndicator.test.tsx` — `it("names the state in text for each status")` | the three strings; `role="status"`; `updates` suffix appears only when > 0 |
| refresher wiring | `web/src/components/alerts/AlertStreamRefresher/AlertStreamRefresher.test.tsx` — `it("refreshes the route when the hook reports an update")` | `vi.mock("next/navigation")` + `vi.mock("@/hooks/useAlertStream")`: the hook receives `url` ending `/api/v1/stream`; invoking the captured `onUpdate` calls `router.refresh` once |
| queue page renders the island | same file — `it("renders the live indicator above the filter bar")` (or an assertion inside the existing queue page test) | `AlertStreamRefresher` appears in `web/src/app/alerts/page.tsx` |

## Steps (TDD)

Roles: the **test-author** does Steps 1–2 and pins the six new test files; the **implementer** does
Steps 3–8 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the six test files** per the table. Python tests import
  from `api.routes.stream` / `core.schemas.stream` (which do not exist yet). For the route tests use
  a REAL `uvicorn.Server` bound to a free `127.0.0.1` port (ruling R12: `httpx.ASGITransport`
  awaits the whole ASGI app coroutine before returning any response, so it can never observe a
  partial body from an endless stream — `client.stream()` hangs exactly as badly as `get()`). Wrap every read in `asyncio.wait_for(..., 5)` so a
  regression fails instead of hanging CI. For the frontend, write a `FakeEventSource` class in the
  hook's test file exposing `addEventListener`, `close`, and an `emit(type, init?)` helper; import
  `describe/it/expect/vi/beforeEach/afterEach` explicitly (`globals: false`) and call
  `vi.unstubAllEnvs()` / `vi.useRealTimers()` in `afterEach`. Format the TS files with
  `pnpm -C web format` (or `npx prettier --write`) before committing.
- [ ] **Step 2 (RED — test-author): prove they fail.**
  `export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0`
  then `uv run pytest -q -rs tests/test_stream_events.py tests/test_stream_route.py` → Expected:
  collection errors `ModuleNotFoundError: No module named 'api.routes.stream'`.
  `pnpm -C web test` → Expected: `Failed to resolve import "@/hooks/useAlertStream"` and
  `"@/lib/api/browser"`. Paste both outputs, `sha256sum` every authored file, commit
  `test(api,web): SSE stream, alert-stream hook, live indicator RED (m8a task-01)` with the two
  trailers.
- [ ] **Step 3 (GREEN — implementer): the schema, the error class and the settings.**
  `core/schemas/stream.py`, `StreamUnavailableError` + its `STATUS_BY_ERROR` row,
  `stream_heartbeat_s` / `stream_max_clients` on `Settings`, and their two `.env.example` lines
  (`STREAM_HEARTBEAT_S=15` and `STREAM_MAX_CLIENTS=50`, each with a one-line comment saying what it
  bounds). Run `uv run mypy --no-incremental` now, not at the end.
- [ ] **Step 4 (GREEN — implementer): `api/routes/stream.py`** — the four pure helpers, `StreamGate`,
  `verdict_event_stream`, the route. Then `api/deps.py::get_redis`/`RedisDep` and the two
  `api/factory.py` lines. `uv run pytest -q -rs tests/test_stream_events.py tests/test_stream_route.py`
  → all pass, 0 skipped.
- [ ] **Step 5 (GREEN — implementer): regenerate the wire surface in this same commit.**
  `uv run python scripts/export_openapi.py` then `pnpm -C web codegen`; both diffs land together
  (CONVENTIONS §8). `uv run pytest -q -rs tests/test_openapi_baseline.py tests/test_openapi_hygiene.py`
  → pass.
- [ ] **Step 6 (GREEN — implementer): the browser side.** `web/src/lib/api/browser.ts`,
  `web/src/hooks/useAlertStream.ts`, the two components (folder-per-component with
  `interface.ts` + `index.ts` barrel), and the one-line change to `web/src/app/alerts/page.tsx`.
  `pnpm -C web type-check` after each file; `pnpm -C web test` → all pass.
- [ ] **Step 7 (implementer): prove it live, locally.** With the dev stack up
  (`docker compose -f infra/docker-compose.yml up -d`, migrations applied):
  ```bash
  curl -N -s -m 20 http://127.0.0.1:8000/api/v1/stream | head -5   # ": heartbeat" lines
  uv run python scripts/post_alert.py fixtures/alerts/alert5.json  # in a second shell
  ```
  Paste the heartbeat lines and the `event: verdict.created` frame the POST produced (redact
  nothing — the fixture carries no secret). If the dev stack is unavailable, say so explicitly in
  the report and paste the integration test's output instead; never claim a live run that did not
  happen.
- [ ] **Step 8 (implementer): full gates, then one commit.**
  ```bash
  export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
  uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
  pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
  ```
  0 skipped. Path-scoped `git add` of exactly the files above, message
  `feat(api,web): SSE verdict stream with heartbeats and a client cap; useAlertStream with 30s polling fallback (m8a task-01)`
  plus the two trailers. Tree clean afterwards; re-verify the six pinned sha256 values and report them.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_stream_events.py tests/test_stream_route.py tests/test_openapi_baseline.py tests/test_openapi_hygiene.py tests/test_env_example_roster.py tests/test_error_status_mapping.py tests/test_cors.py   # all pass, 0 skipped
uv run lint-imports                                        # Contracts: N kept, 0 broken
grep -rn "import worker\|from worker\|core.llm" api/        # no hits (PRD §10.1)
pnpm -C web codegen && git diff --exit-code -- web/src/types/generated && echo no-drift
pnpm -C web lint && pnpm -C web type-check && pnpm -C web format:check && pnpm -C web test
grep -rn "#[0-9a-fA-F]\{6\}" web/src --include='*.tsx' --include='*.ts'; echo "exit=$?"   # exit=1 (hex only in globals.css)
```

## Acceptance

- `GET /api/v1/stream` answers `200 text/event-stream`, emits a `verdict.created` frame for every
  message published on `sentinelbrief:verdict.created`, and emits heartbeat comments while idle;
  it answers `503 stream_unavailable` with no Redis wired and `429 rate_limited` past
  `STREAM_MAX_CLIENTS`, both in the PRD §8 envelope.
- A `summary` containing newlines cannot forge a second SSE event — pinned by a test that would
  fail if the body were ever interpolated instead of JSON-serialized.
- `/alerts` shows a live indicator, refreshes itself on each event without the payload being
  rendered, falls back to 30 s polling when the stream errors, and returns to live when it reopens.
- The OpenAPI baseline and the generated web types moved in the same commit; `lint-imports` is
  green and nothing under `api/` imports `worker` or `core.llm`.
