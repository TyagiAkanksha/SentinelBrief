# fixtures/cowrie/

Task-02's replay log lands here: `cowrie.json`, JSON Lines (one event object per line, the same
shape `.claude/skills/cowrie-fixture/references/cowrie-events.md` documents), fully synthetic —
never a real attacker payload or a raw log copied off `hp-use-01`. It exercises the shipper
(task-02) end to end without a live honeypot host: tailing, session grouping on
`cowrie.session.closed`, and signed posting to the ingest endpoint.

`cowrie.json` holds 23 lines and four sessions, all on sensor `hp-use-01` with source/destination
IPs drawn from the RFC 5737 documentation ranges (`198.51.100.0/24` attackers,
`203.0.113.5`/`203.0.113.9` honeypot/download host): session **A** (`a1b2c3d4e5f6`, 11 events —
connect, client.version, client.size, login.failed x2, login.success, command.input x3, a
file_download, and a `session.closed` carrying `duration_ms`) closes LAST; session **B**
(`b2c3d4e5f6a7`, 6 events — connect, client.version, login.failed x3, closed) closes FIRST and its
events are interleaved with A's in timestamp order, so the shipper's replay must emit B's payload
before A's; session **C** (`c3d4e5f6a7b8`, 3 events — connect, client.version, login.failed) is
never closed, exercising the idle-flush path; session **D** (`d4e5f6a7b8c9`, 2 events — a bare
`command.input` then a `session.closed`, no connect) simulates the shipper starting mid-session and
must be dropped, never shipped. Line 17 is one truncated/malformed JSON line
(`{"eventid": "cowrie.`) the assembler must skip and count, never crash on. One `cowrie.client.size`
event (an eventid outside `cowrie-events.md`'s summarized table) sits inside session A to prove
unlisted event types still flow through untouched. Every command, credential, banner and URL in
the file is synthetic; the five strings `tests/test_shipper_assemble.py::_ATTACKER_STRINGS` pins
absent from shipper log output (`uname -a`, `cat /etc/passwd`, `203.0.113.9/x.sh`, `libssh2`,
`[root/123456]`) live only in this fixture's event payloads, never in any log record the shipper
itself emits.
