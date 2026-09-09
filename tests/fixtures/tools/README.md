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
  "result": {"asn": 64512, "country": "ZZ"}
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
