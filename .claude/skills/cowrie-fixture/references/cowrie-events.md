# Cowrie JSON event reference

Verified against upstream `cowrie/cowrie` `docs/OUTPUT.rst` on 2026-09-06. Cowrie writes one JSON
object per line to `var/log/cowrie/cowrie.json` (the JSON output plugin; path configurable).

## Attributes on every event

| Field | Meaning |
|---|---|
| `eventid` | event identifier, e.g. `cowrie.session.connect` |
| `message` | human-readable description |
| `sensor` | sensor name, defaults to the hostname |
| `timestamp` | ISO 8601, UTC, microsecond precision, `Z` suffix |
| `session` | unique session identifier (hex string) — SentinelBrief's `session_id` |
| `src_ip` | attacker IP |

Connection-scoped events additionally carry `protocol` (`ssh` or `telnet`), `src_port`,
`dst_ip`, `dst_port`.

## Per-event fields (the ones SentinelBrief reads)

| Event id | Fields | SentinelBrief use |
|---|---|---|
| `cowrie.session.connect` | `src_ip, src_port, dst_ip, dst_port, protocol` | must be `events[0]`; its `timestamp` is the alert's `connect_time` and part of the fingerprint |
| `cowrie.client.version` | `version` (client SSH banner) | `SessionSummary.client_version` — attacker-controlled, delimited |
| `cowrie.client.kex` | `hassh, hasshAlgorithms, kexAlgs, keyAlgs, encCS, macCS, compCS, langCS` | kept in `raw`; `hassh` is a useful client fingerprint for M4+ |
| `cowrie.login.failed` | `username, password, fingerprint, key, type` | `login_failed` count, `usernames_sample` |
| `cowrie.login.success` | `username, password, fingerprint, key, type` | `login_success` count, `first_success_credential` |
| `cowrie.command.input` | `input, realm` | `command_count`; full list via `get_session_commands` (M4) |
| `cowrie.command.failed` | `input` | counted with commands |
| `cowrie.session.file_download` | `url, outfile, shasum, destfile, duplicate` | `download_count`; `downloads` in `get_session_commands` |
| `cowrie.session.file_upload` | `filename, outfile, shasum, destfile, duplicate` | `upload_count` |
| `cowrie.session.closed` | `duration_ms` (milliseconds) | the shipper's "post now" trigger; `duration_ms` on the alert |
| `cowrie.log.closed` | `ttylog, size, duration_ms, shasum, duplicate` | kept in `raw` |

Note the duration field is `duration_ms`, in **milliseconds** — not `duration`. Older
third-party examples show `duration` in seconds; do not copy them.

## Other events Cowrie emits (kept in `raw`, not summarized)

`cowrie.client.size`, `cowrie.client.var`, `cowrie.client.fingerprint`, `cowrie.direct-tcpip.request`,
`cowrie.direct-tcpip.data`, `cowrie.session.params`, `cowrie.command.success`. Include them only
when a realistic session would have them; never invent field names for them.

## Minimal realistic session (severity 4)

```json
[
  {"eventid":"cowrie.session.connect","timestamp":"2026-09-06T14:03:21.481902Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","src_port":51522,"dst_ip":"10.0.0.5","dst_port":22,"protocol":"ssh","sensor":"hp-sgp-01","message":"New connection: 203.0.113.42:51522 (10.0.0.5:22) [session: a1b2c3d4e5f6]"},
  {"eventid":"cowrie.client.version","timestamp":"2026-09-06T14:03:21.702118Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","sensor":"hp-sgp-01","version":"SSH-2.0-libssh2_1.10.0","message":"Remote SSH version: SSH-2.0-libssh2_1.10.0"},
  {"eventid":"cowrie.login.failed","timestamp":"2026-09-06T14:03:22.115004Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","sensor":"hp-sgp-01","username":"root","password":"root","message":"login attempt [root/root] failed"},
  {"eventid":"cowrie.login.success","timestamp":"2026-09-06T14:03:23.004311Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","sensor":"hp-sgp-01","username":"root","password":"123456","message":"login attempt [root/123456] succeeded"},
  {"eventid":"cowrie.command.input","timestamp":"2026-09-06T14:03:24.221907Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","sensor":"hp-sgp-01","input":"uname -a","message":"CMD: uname -a"},
  {"eventid":"cowrie.command.input","timestamp":"2026-09-06T14:03:25.980032Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","sensor":"hp-sgp-01","input":"cat /etc/passwd","message":"CMD: cat /etc/passwd"},
  {"eventid":"cowrie.session.closed","timestamp":"2026-09-06T14:03:31.104450Z","session":"a1b2c3d4e5f6","src_ip":"203.0.113.42","sensor":"hp-sgp-01","duration_ms":9622,"message":"Connection lost after 9 seconds"}
]
```
