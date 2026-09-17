# Tool fixtures (PRD §7.2)

Recorded results for **external** tools (`Tool.external is True`), replayed by
`worker.tools.ReplayToolRecorder` so tests and evals are deterministic and never call a live API
(CLAUDE.md, CONVENTIONS.md §10). Local (`external=False`) tools never need a fixture — they always
run live, deterministically, from `ctx.alert` and repo files.

## Layout

```
tests/fixtures/tools/<tool_name>/<key>.json
```

`<tool_name>` is the tool's `name` (e.g. `get_ip_geo_asn`, `lookup_ip_reputation`). `<key>` is
`worker.tools.fixture_key(arguments)`:

```python
sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)).hexdigest()[:16]
```

The key is order-independent (`{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` derive the same key) and
depends only on the tool-call arguments, so the same arguments always resolve to the same fixture
file regardless of which test or eval run wrote it.

## File body

```json
{
  "tool": "get_ip_geo_asn",
  "arguments": {"ip": "203.0.113.10"},
  "result": {"asn": 64496, "country": "ZZ"}
}
```

`result` is the tool's **normalized result** — the same shape `Tool.run` returns and the model
sees — never a raw provider response body (`.claude/rules/tests.md`). `ReplayToolRecorder` checks
`tool` and `arguments` against the file it loads and reports `unavailable("fixture_mismatch")` if
either disagrees, so a stale or hand-edited key can never silently serve the wrong fixture.

## Minting a fixture

Either call `worker.tools.write_fixture` directly, or run the tool once through a
`LiveToolRecorder` pointed at this directory:

```python
from pathlib import Path
from worker.tools import LiveToolRecorder

recorder = LiveToolRecorder(record_dir=Path("tests/fixtures/tools"))
result = await recorder.execute(tool, arguments, ctx)  # also writes the fixture file
```

### Recording a golden set's fixtures (`evals/record.py`, m7 task-02)

`python -m evals.record --golden <golden-file> [--fixtures tests/fixtures/tools]
[--only get_ip_geo_asn] [--dry-run]` walks every case in `<golden-file>` and mints exactly one
fixture per distinct `(tool, src_ip)` pair for `get_ip_geo_asn`/`lookup_ip_reputation` — the only
two external tools whose complete argument set a `GoldenCase` alone determines, so recording can
enumerate every fixture they will ever need (ruling R26). `get_alert_history` is also
`external = True` and replays from a fixture too, but its `window_hours` argument is the model's
free choice — its fixture space is unbounded, so `evals.record` never mints one for it and
`evals.run --replay-strict` always replays it leniently (`unavailable("fixture_missing")` on a
miss, never a raise), strict or not, so a v2 run's exit code can never depend on which window the
model happened to pick. `evals.record` skips any fixture that already exists, so it is safe to
re-run after adding rows. `--dry-run` prints the plan (`<tool> <ip>` per line) and calls nothing.

An `unavailable(reason)` result whose `reason` means "the OWNER's environment couldn't answer"
(`worker.tools.TRANSIENT_REASONS` — no API key, no `.mmdb`, a quota hit, a network blip, ...) is
never persisted as a fixture: `evals.record` removes the file and reports the call as failed
instead, so a partial run against an unconfigured environment can never poison a committed
fixture (ruling R25). A DETERMINISTIC `unavailable(reason)` (e.g. `invalid_arguments`, produced by
the tool's own logic rather than the environment) is persisted like a real answer, because it
reproduces identically forever. `ReplayToolRecorder(strict=True)` additionally refuses to SERVE a
fixture recorded with a transient reason, even one hand-written to disk — never assume good faith
of a `tests/fixtures/tools/*.json` file that reads `{"unavailable": true, "reason": "no_api_key"}`
et al.

For a real v2 run against the live APIs (the OWNER's step, never CI's): count the calls first with
`--dry-run` (MaxMind is free; AbuseIPDB's free tier has a daily quota — one call per distinct
`src_ip`), then run without `--dry-run` on a workstation with the real `.env` keys set
(`GEOIP_DB_PATH`/`GEOIP_ASN_DB_PATH` pointing at the local `.mmdb` files, `ABUSEIPDB_API_KEY`
set). `evals.run --golden <golden-file> ...` defaults to `--replay-strict` for a `v2*`-named file
(PRD §13) — any of the two ip tools' fixtures this step didn't mint fails that run loudly, naming
the missing `(tool, key)` pairs, rather than silently scoring degraded tool evidence.

To compute a fixture's key by hand (e.g. to name a file before writing it, or to check an
existing one):

```bash
uv run python -c "from worker.tools import fixture_key; print(fixture_key({'ip': '203.0.113.10'}))"
# 5d2e7bda8feb939e
```

## Content rule

M4's fixtures are synthetic: documentation IPs (`TEST-NET-*`, RFC 5737 — `203.0.113.0/24`,
`198.51.100.0/24`, `192.0.2.0/24`) and RFC 5398 documentation ASNs. Never commit a real attacker
IP, a real provider API response, or a real API key/secret into this directory.
