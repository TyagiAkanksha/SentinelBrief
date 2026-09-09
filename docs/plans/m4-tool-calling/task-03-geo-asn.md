---
id: task-03
milestone: m4-tool-calling
depends_on: [task-01]
status: planned
spec: PRD.md §6.3 (`get_ip_geo_asn(ip) -> {country, asn, org}`: local MaxMind GeoLite2, no network call; the `.mmdb` is downloaded at deploy time with a free license key and is never committed; without it `{unavailable: true}`), §10.8 (the MaxMind key is a deploy-time secret; the `.mmdb` is never committed), §9 (country flag — consumed by task-07), §13 (MaxMind account is the owner's; ship the unavailable path); CONVENTIONS.md §7 (secrets are `SecretStr`), §10 (`@pytest.mark.live` for anything external); `.claude/rules/{worker,infra,tests}.md`
---

# task-03 — `get_ip_geo_asn` over MaxMind GeoLite2 (Country + ASN readers, unavailable path), `scripts/fetch_geoip.py` (never commits a database), compose mount, recorded geo fixtures for the five fixture IPs

## Goal

`GeoAsnTool` answers `{ip, country, asn, org}` from two local GeoLite2 databases opened once at
construction — `GEOIP_DB_PATH` (GeoLite2-Country; a City file works too, both carry
`country.iso_code`) and `GEOIP_ASN_DB_PATH` (GeoLite2-ASN — the Country/City editions carry no
ASN, so PRD §6.3's `asn`/`org` need the second file). Either path empty or unreadable degrades:
that half of the answer is `null`; both missing is `{unavailable: true, reason:
"geoip_db_not_configured"}`, logged once. The reader is a one-method Protocol so unit tests use an
in-memory fake (the DB file is an external seam — CONVENTIONS §10 — and no `.mmdb` may ever be
committed, so no test fixture file can exist). `scripts/fetch_geoip.py` downloads both editions
with `MAXMIND_LICENSE_KEY` into `infra/geoip/` (gitignored by `*.mmdb`, dockerignored likewise),
never prints the key or the URL that carries it, and refuses to run without the key before any
network call; the compose `api` service bind-mounts that directory read-only at the same
relative path so one `GEOIP_DB_PATH` string works on the host and in the container. Synthetic
geo fixtures for the five `fixtures/alerts` IPs land under `tests/fixtures/tools/get_ip_geo_asn/`
(documentation IPs, RFC 5398 documentation ASNs) so the seed's replay (task-06) and the flag
(task-07) have data.

## Context (read ONLY these)

- `PRD.md` §6.3 (geo row), §10.8, §11 (deploy-time fetch into a volume), §13.
- `docs/plans/m4-tool-calling.md` — Global Constraints (unkeyed/missing `.mmdb` → unavailable,
  log once; recorded fixtures; `.mmdb` never committed).
- `CONVENTIONS.md` §4, §7, §10; `.claude/rules/worker.md`, `.claude/rules/infra.md` (no secret
  values anywhere; `*.mmdb` gitignored and dockerignored), `.claude/rules/tests.md`.
- Task-01 outputs: `worker/tools/base.py`, `worker/tools/recorder.py` (`write_fixture`,
  `fixture_key`, `ReplayToolRecorder`), `tests/fixtures/tools/README.md`.
- Code you build on: `core/config.py`, `.env.example` (`MAXMIND_LICENSE_KEY`, `GEOIP_DB_PATH`
  lines exist), `tests/test_env_example_roster.py` (`_SCHEDULED`), `scripts/post_alert.py` +
  `tests/test_post_alert.py` (the `main(argv, *, transport=…)` seam and `importlib` loading
  pattern to copy), `infra/docker-compose.yml` + `tests/test_compose_config.py`
  (`_render_compose_config` helper), `.gitignore` (`*.mmdb`), `.dockerignore` (`*.mmdb`),
  `README.md` (step 1 `set -a; . ./.env; set +a` — host-side commands read the environment only;
  `Settings(env_file=None)` in `core/config.py` never loads `.env`).
- `maxminddb 3.1.1` (PyPI 2026-03-05, Apache-2.0, `py.typed` + cp312 manylinux wheel, not
  yanked): `maxminddb.open_database(path)` → `Reader`; `Reader.get(ip: str) -> dict | None`
  (`None` for an address not in the DB — every documentation-range IP); raises `ValueError` on a
  malformed address and `maxminddb.InvalidDatabaseError` on a corrupt file. Country/City records
  carry `{"country": {"iso_code": "NL", ...}}`; ASN records carry
  `{"autonomous_system_number": 64496, "autonomous_system_organization": "..."}`.
- MaxMind download permalink (verified against MaxMind's GeoIP download documentation at
  briefing time; the live test re-verifies): `GET https://download.maxmind.com/app/geoip_download
  ?edition_id=<edition>&license_key=<key>&suffix=tar.gz` → a `.tar.gz` whose only `.mmdb` member
  is `<edition>_<YYYYMMDD>/<edition>.mmdb`.

## Files

- Create: `worker/tools/geo_asn.py`, `scripts/fetch_geoip.py`, `infra/geoip/.gitkeep`,
  `tests/fixtures/tools/get_ip_geo_asn/5d2e7bda8feb939e.json` (203.0.113.10),
  `…/6a624fe81e1c51a3.json` (198.51.100.23), `…/1ba86fc94704aedc.json` (203.0.113.77),
  `…/70c94a209a3ec9bd.json` (192.0.2.55), `…/55230db792e5f6bf.json` (198.51.100.140)
- Create (test-author): `tests/test_geo_asn_tool.py`, `tests/test_fetch_geoip.py`,
  `tests/test_geo_live.py`
- Modify (test-author, re-pinned): `tests/test_env_example_roster.py` (`MAXMIND_LICENSE_KEY`,
  `GEOIP_DB_PATH` leave `_SCHEDULED`), `tests/test_compose_config.py` (one new test)
- Modify: `core/config.py`, `.env.example`, `pyproject.toml` + `uv.lock` (`maxminddb`),
  `infra/docker-compose.yml`, `worker/tools/__init__.py`, `README.md` (one paragraph)

## Interfaces

- **Consumes:** `Tool`, `ToolContext`, `unavailable` (`worker.tools.base`); `write_fixture`,
  `fixture_key`, `ReplayToolRecorder` (`worker.tools.recorder`); `Settings`; `SecretStr`;
  `tests.test_compose_config._render_compose_config`.
- **Produces (task-06 wiring, task-07 flag, M6 deploy rely on — produce exactly):**

  ```python
  # worker/tools/geo_asn.py — the ONLY module that imports `maxminddb`
  class GeoReader(Protocol):
      def get(self, ip: str) -> Mapping[str, Any] | None: ...          # maxminddb.Reader.get's contract (ValueError on a malformed ip)
  def open_reader(path: str) -> GeoReader | None: ...
      # "" -> None (not configured); OSError / maxminddb.InvalidDatabaseError -> None + logger.warning once (the path, never contents)
  class GeoAsnTool:
      name = "get_ip_geo_asn"
      external = True                                                  # depends on a downloaded DB -> replayed from fixtures in evals/tests
      description = "Look up the country, autonomous system number and organisation of an IP address from a local GeoLite2 database (no network)."
      parameters = {"type": "object", "properties": {"ip": {"type": "string", "description": "An IPv4 or IPv6 address."}},
                    "required": ["ip"], "additionalProperties": False}
      def __init__(self, *, country: GeoReader | None, asn: GeoReader | None) -> None: ...
      @classmethod
      def from_settings(cls, settings: Settings) -> GeoAsnTool: ...    # cls(country=open_reader(settings.geoip_db_path), asn=open_reader(settings.geoip_asn_db_path))
      async def run(self, arguments, ctx) -> dict[str, Any]: ...
          # ip missing / not str / ipaddress.ip_address(ip) raises -> unavailable("invalid_arguments")
          # both readers None                                        -> unavailable("geoip_db_not_configured"), logger.warning once per instance
          # country = reader.get(ip)["country"]["iso_code"] when the record and key exist else None   (registered_country is not consulted)
          # asn/org from the ASN reader's autonomous_system_number / autonomous_system_organization, else None
          # a reader raising ValueError -> unavailable("invalid_arguments"); maxminddb.InvalidDatabaseError -> unavailable("geoip_db_error")
          # -> {"ip": ip, "country": str | None, "asn": int | None, "org": str | None}
          # `country`, when present, is exactly the DB's iso_code (2 uppercase ASCII letters) — task-07 renders the flag from it

  # core/config.py — new fields (+ .env.example lines under "Enrichment tools (from M4)")
  maxmind_license_key: SecretStr = SecretStr("")   # MAXMIND_LICENSE_KEY=            (line exists; graduates; SECRET, deploy-time only — CONVENTIONS §7)
  geoip_db_path: str = ""                          # GEOIP_DB_PATH=                  (line exists; graduates; comment reworded: GeoLite2 Country (or City) .mmdb;
                                                   #   relative to the process cwd, e.g. infra/geoip/GeoLite2-Country.mmdb on the host AND in the container)
  geoip_asn_db_path: str = ""                      # GEOIP_ASN_DB_PATH=              (new; GeoLite2-ASN .mmdb, e.g. infra/geoip/GeoLite2-ASN.mmdb; empty -> asn/org null)

  # scripts/fetch_geoip.py — deploy-time helper (PRD §11); runs on the host / the box, never in the image
  EDITIONS = ("GeoLite2-Country", "GeoLite2-ASN")
  DOWNLOAD_URL = "https://download.maxmind.com/app/geoip_download"
  DEFAULT_OUT_DIR = Path("infra/geoip")
  def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int: ...
      # flags: --out-dir DIR (default DEFAULT_OUT_DIR), --edition NAME (repeatable; default both EDITIONS; unknown -> usage error)
      # key = Settings().maxmind_license_key.get_secret_value(); "" -> stderr `error: config_error: MAXMIND_LICENSE_KEY is not set`, exit 1,
      #   BEFORE any network access (the transport must never be invoked)
      # per edition: GET DOWNLOAD_URL with params edition_id, license_key, suffix=tar.gz (httpx.Client(transport=transport, timeout=60, follow_redirects=True))
      #   non-2xx                     -> `error: download_failed: <edition>: HTTP <status>`      exit 1
      #   httpx.HTTPError             -> `error: download_failed: <edition>: <ExceptionClassName>` exit 1   (the exception TEXT can embed the URL
      #                                  and therefore the key — only the class name is ever printed)
      #   tarfile: extract ONLY the member whose name ends with f"/{edition}.mmdb" (or equals it), stripped of its directory, to
      #     out_dir / f"{edition}.mmdb" (mkdir parents; a member path is never used verbatim — no path traversal); no such member ->
      #     `error: download_failed: <edition>: no .mmdb in archive` exit 1; tarfile.TarError -> `error: download_failed: <edition>: bad archive`
      #   success line per edition: f"{edition}: {size} bytes -> {path}"; exit 0 after all editions
      # The key appears in the request query only. stdout/stderr never contain it; nothing is logged.
  if __name__ == "__main__": raise SystemExit(main())

  # infra/docker-compose.yml — `api` service gains
  #   volumes:
  #     - ../infra/geoip:/app/infra/geoip:ro     # GeoLite2 .mmdb files fetched by scripts/fetch_geoip.py; same relative path as on the host
  # infra/geoip/.gitkeep keeps the directory tracked (empty) so the bind mount never creates a root-owned dir on first `up`.

  # tests/fixtures/tools/get_ip_geo_asn/<key>.json — synthetic, per fixtures/alerts (keys verified at briefing time with fixture_key):
  #   203.0.113.10   -> {"ip": "203.0.113.10",   "country": "NL", "asn": 64496, "org": "Example Scanning BV"}     5d2e7bda8feb939e
  #   198.51.100.23  -> {"ip": "198.51.100.23",  "country": "US", "asn": 64497, "org": "Example Cloud LLC"}       6a624fe81e1c51a3
  #   203.0.113.77   -> {"ip": "203.0.113.77",   "country": "SG", "asn": 64498, "org": "Example Hosting Pte"}     1ba86fc94704aedc
  #   192.0.2.55     -> {"ip": "192.0.2.55",     "country": "DE", "asn": 64499, "org": "Example Hosting GmbH"}    70c94a209a3ec9bd
  #   198.51.100.140 -> {"ip": "198.51.100.140", "country": "BR", "asn": 64500, "org": "Example Telecom SA"}      55230db792e5f6bf
  #   each file: {"tool": "get_ip_geo_asn", "arguments": {"ip": ...}, "result": {...}} — written with write_fixture so the layout cannot drift

  # README.md — one new paragraph at the end of "3. Run the stack" titled "Enrichment tools (optional)":
  #   `uv run python scripts/fetch_geoip.py`   # needs MAXMIND_LICENSE_KEY exported (step 1); writes infra/geoip/GeoLite2-Country.mmdb and GeoLite2-ASN.mmdb, never committed
  #   then set GEOIP_DB_PATH=infra/geoip/GeoLite2-Country.mmdb and GEOIP_ASN_DB_PATH=infra/geoip/GeoLite2-ASN.mmdb in .env (the compose api mounts
  #   infra/geoip read-only at the same path); ABUSEIPDB_API_KEY is optional too (task-04). Without keys both tools answer {"unavailable": true}.
  #   Plan-defect rule 1: the implementer runs the fetch line from a fresh shell — `env -i HOME="$HOME" PATH="$PATH" bash --noprofile --norc -c
  #   'cd <repo> && uv run python scripts/fetch_geoip.py'` — and pastes the exact output (`error: config_error: MAXMIND_LICENSE_KEY is not set`,
  #   exit 1) into the report before the paragraph is written; with a key present, the two success lines.
  ```

## Interfaces → test table

`FakeGeoReader(dict[str, Mapping | None])` in `tests/test_geo_asn_tool.py` implements `GeoReader`
(`get` returns the mapped record, `None` for an unknown IP, raises `ValueError` for `"bad"`).

| Interfaces line | test file::test name | failure branch covered |
|---|---|---|
| full answer | `tests/test_geo_asn_tool.py::test_country_asn_org_from_both_readers` | country reader `{"country": {"iso_code": "NL"}}`, ASN reader `{"autonomous_system_number": 64496, "autonomous_system_organization": "Example"}` → `{"ip", "country": "NL", "asn": 64496, "org": "Example"}` |
| country only | `tests/test_geo_asn_tool.py::test_asn_reader_missing_gives_null_asn_and_org` | `asn=None` → `asn is None and org is None`, country still `"NL"`; fails when a missing ASN reader makes the whole result unavailable |
| ASN only | `tests/test_geo_asn_tool.py::test_country_reader_missing_gives_null_country` | mirror of the above |
| both missing → unavailable, logs once | `tests/test_geo_asn_tool.py::test_no_readers_is_unavailable_and_logs_once` | `unavailable("geoip_db_not_configured")` on two calls; `caplog` has exactly one WARNING; fails when logged per call |
| IP not in DB | `tests/test_geo_asn_tool.py::test_unknown_ip_gives_null_fields_not_unavailable` | readers return `None` → `{"country": None, "asn": None, "org": None}` (a lookup that found nothing is still a successful lookup) |
| record without iso_code | `tests/test_geo_asn_tool.py::test_record_without_iso_code_gives_null_country` | `{"country": {}}` → `country is None`; no `KeyError` |
| invalid ip | `tests/test_geo_asn_tool.py::test_invalid_ip_is_invalid_arguments` | `"not-an-ip"`, `7`, missing → `unavailable("invalid_arguments")`, readers never consulted |
| reader ValueError | `tests/test_geo_asn_tool.py::test_reader_value_error_is_invalid_arguments` | reader raising `ValueError` → `invalid_arguments`, never propagates |
| corrupt DB | `tests/test_geo_asn_tool.py::test_invalid_database_error_is_geoip_db_error` | reader raising `maxminddb.InvalidDatabaseError` → `unavailable("geoip_db_error")` |
| `open_reader` | `tests/test_geo_asn_tool.py::test_open_reader_empty_missing_and_corrupt_paths_return_none` | `""` → `None` silently; `tmp_path/"missing.mmdb"` → `None` + one WARNING; a 16-byte garbage file → `None` + one WARNING (`InvalidDatabaseError`); fails when any raises |
| `from_settings` | `tests/test_geo_asn_tool.py::test_from_settings_uses_both_paths` | `Settings(geoip_db_path="", geoip_asn_db_path="")` → tool whose `run` is `geoip_db_not_configured` |
| `external` flag | `tests/test_geo_asn_tool.py::test_tool_is_external` | `GeoAsnTool.external is True` (replay serves it from fixtures) |
| settings + secret | `tests/test_geo_asn_tool.py::test_geo_settings_and_secret_repr` | defaults `""`/`""`; `repr(Settings(maxmind_license_key=SecretStr("k-1")))` does not contain `k-1`; roster test green after the two names leave `_SCHEDULED` |
| recorded fixtures replay | `tests/test_geo_asn_tool.py::test_recorded_fixtures_replay_for_the_five_fixture_ips` | `ReplayToolRecorder(Path("tests/fixtures/tools"))` over the five fixture IPs → the five countries `NL, US, SG, DE, BR`; each file's `arguments == {"ip": <ip>}` and its name is `fixture_key(arguments)`; fails when a file is renamed or a key mismatches |
| fetch refuses without key, no network | `tests/test_fetch_geoip.py::test_refuses_without_license_key_before_any_request` | `monkeypatch.delenv("MAXMIND_LICENSE_KEY")`; a `MockTransport` handler that fails the test if called; exit 1; stderr exactly `error: config_error: MAXMIND_LICENSE_KEY is not set`; stdout empty |
| fetch downloads both editions | `tests/test_fetch_geoip.py::test_downloads_both_editions_into_out_dir` | handler serves an in-memory `tar.gz` built with `tarfile` containing `<edition>_20260901/<edition>.mmdb` (dummy bytes) + `COPYRIGHT.txt`; exit 0; `out_dir/GeoLite2-Country.mmdb` and `GeoLite2-ASN.mmdb` exist with the dummy bytes; no `COPYRIGHT.txt` extracted; stdout two `<edition>: <n> bytes -> <path>` lines |
| key in query only | `tests/test_fetch_geoip.py::test_license_key_is_sent_in_the_query_and_never_printed` | handler records `request.url.params["license_key"] == "test-key"` and `edition_id`, `suffix=tar.gz`; `"test-key"` appears in neither stdout nor stderr nor `caplog.text`; fails when the URL is echoed |
| non-2xx | `tests/test_fetch_geoip.py::test_http_error_status_exits_1_without_the_key` | 403 → exit 1, stderr `error: download_failed: GeoLite2-Country: HTTP 403`, no key |
| transport exception | `tests/test_fetch_geoip.py::test_transport_exception_prints_only_the_class_name` | handler raising `httpx.ConnectError("boom https://…license_key=test-key")` → stderr `error: download_failed: GeoLite2-Country: ConnectError`; `"test-key"` absent |
| archive without mmdb / path traversal | `tests/test_fetch_geoip.py::test_archive_without_mmdb_or_with_traversal_name_is_rejected` | a tarball with only `README.txt` → `no .mmdb in archive`; a member named `../../evil.mmdb` does not match the edition rule → `no .mmdb in archive` and no `.mmdb` is written anywhere; a member named `../../GeoLite2-Country.mmdb` matches (ends with `/GeoLite2-Country.mmdb`) and is written to `out_dir/GeoLite2-Country.mmdb` by basename with nothing outside `out_dir`; fails when the member path is honored (controller ruling R8: the Interfaces matching rule is authoritative; this row previously contradicted it) |
| `--edition` filter | `tests/test_fetch_geoip.py::test_edition_flag_limits_downloads` | `--edition GeoLite2-ASN` → one request, one file; `--edition Bogus` → usage error exit 1 |
| never committed | `tests/test_fetch_geoip.py::test_mmdb_is_gitignored_and_dockerignored` | `.gitignore` and `.dockerignore` each contain a line `*.mmdb`; `git ls-files '*.mmdb'` (subprocess) prints nothing |
| compose mount | `tests/test_compose_config.py::test_compose_api_mounts_geoip_read_only` | rendered `api.volumes` contains a bind with `target == "/app/infra/geoip"`, `read_only is True`, source resolving to `<tmp>/infra/geoip`; fails when writable or at another path |
| live: real DB | `tests/test_geo_live.py::test_live_country_lookup_with_the_downloaded_database` (`@pytest.mark.live`) | skips unless `GEOIP_DB_PATH` is set and the file exists; `GeoAsnTool.from_settings(Settings())` on `1.1.1.1` → `country` matches `^[A-Z]{2}$` |
| live: real download | `tests/test_geo_live.py::test_live_fetch_geoip_downloads_a_readable_database` (`@pytest.mark.live`) | skips unless `MAXMIND_LICENSE_KEY` is set; `main(["--out-dir", str(tmp_path), "--edition", "GeoLite2-Country"])` → exit 0 and `open_reader(...)` is not `None`; never prints the key |

## Steps (TDD)

Roles: the **test-author** writes Steps 1–2 and pins the three new test files plus the two
re-pinned files; the **implementer** does Steps 3–6 and never edits a pinned file.

- [ ] **Step 1 (RED — test-author): write the three test files** per the table (`tests/test_fetch_geoip.py`
  loads the script with `importlib.util.spec_from_file_location` like `tests/test_post_alert.py`
  and builds its tarballs with `tarfile` + `io.BytesIO`); remove `"MAXMIND_LICENSE_KEY"` and
  `"GEOIP_DB_PATH"` from `_SCHEDULED`; add `test_compose_api_mounts_geoip_read_only` to
  `tests/test_compose_config.py`.
- [ ] **Step 2 (RED — test-author): run to see them fail.** `uv run pytest -q tests/test_geo_asn_tool.py
  tests/test_fetch_geoip.py` → Expected: `ModuleNotFoundError: No module named
  'worker.tools.geo_asn'` and `FileNotFoundError: … scripts/fetch_geoip.py`;
  `uv run pytest -q tests/test_compose_config.py -k geoip` → `KeyError: 'volumes'` (or an empty
  list); the roster test names the two graduated variables. `uv run pytest -q -m live
  tests/test_geo_live.py` → both skipped with their reasons (record it). Pin, commit
  `test(worker,scripts): geo/ASN tool, fetch_geoip, geoip mount RED (m4 task-03)`.
- [ ] **Step 3 (GREEN — implementer): `uv add maxminddb>=3` (lock committed), `core/config.py`
  fields, `.env.example` lines/comments, `infra/geoip/.gitkeep`, compose mount.**
  `docker compose -f infra/docker-compose.yml config > /dev/null && echo ok` → `ok` (never paste
  the config output — rules/infra.md).
- [ ] **Step 4 (GREEN — implementer): `worker/tools/geo_asn.py` + re-export; the five fixture files
  via a one-off `write_fixture` loop** (paste the loop and `ls tests/fixtures/tools/get_ip_geo_asn`
  into the report). `uv run mypy` clean.
- [ ] **Step 5 (GREEN — implementer): `scripts/fetch_geoip.py`**; `uv run mypy scripts/fetch_geoip.py`
  clean (outside the gate, pasted anyway). Run the README line from a fresh shell without a key
  and paste `error: config_error: MAXMIND_LICENSE_KEY is not set` / `exit=1`; with a key if the
  owner has exported one, paste the two success lines and `ls -la infra/geoip` (sizes only), then
  `git status --short` → no `.mmdb` listed. Write the README paragraph only after that.
- [ ] **Step 6 (implementer): all tests in the table + the existing suite green; full gates →
  commit:** `feat(worker,scripts): get_ip_geo_asn over GeoLite2 + fetch_geoip.py, geoip mount, geo fixtures (m4 task-03)`
  with the two trailers; path-scoped `git add worker/tools scripts/fetch_geoip.py core/config.py
  .env.example pyproject.toml uv.lock infra/docker-compose.yml infra/geoip/.gitkeep
  tests/fixtures/tools/get_ip_geo_asn README.md`. `git status --short | grep -c mmdb` → `0`.

## Verify

```bash
export TEST_DATABASE_URL=postgresql://sentinel:sentinel@127.0.0.1:5434/sentinelbrief_test
uv run pytest -q tests/test_geo_asn_tool.py tests/test_fetch_geoip.py tests/test_compose_config.py tests/test_env_example_roster.py   # every test in the table passes
uv run pytest -q -m live tests/test_geo_live.py                                    # both skipped without GEOIP_DB_PATH / MAXMIND_LICENSE_KEY; both pass with them
git ls-files '*.mmdb' | wc -l                                                      # 0
ls tests/fixtures/tools/get_ip_geo_asn | wc -l                                     # 5
env -i HOME="$HOME" PATH="$PATH" bash --noprofile --norc -c 'uv run python scripts/fetch_geoip.py; echo "exit=$?"'   # error: config_error: MAXMIND_LICENSE_KEY is not set / exit=1
uv run ruff check --no-cache . && uv run ruff format --check . && uv run mypy --no-incremental && uv run lint-imports && uv run pytest -q   # all clean
```

## Acceptance

- `get_ip_geo_asn` returns `{ip, country, asn, org}` from the two local readers, `null`s the half
  whose database is absent, is `geoip_db_not_configured` (logged once) with neither, treats an
  unknown address as nulls not unavailability, and never raises; `maxminddb` is imported in
  exactly one module.
- `scripts/fetch_geoip.py` refuses without `MAXMIND_LICENSE_KEY` before touching the network,
  downloads both editions into `infra/geoip/`, extracts only the `.mmdb` by basename, and never
  prints the key or a URL; no `.mmdb` is tracked; the compose `api` mounts `infra/geoip`
  read-only at the same relative path.
- Five synthetic geo fixtures replay through `ReplayToolRecorder` for the fixture alerts' IPs.
