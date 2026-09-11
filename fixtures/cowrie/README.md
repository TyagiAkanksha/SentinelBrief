# fixtures/cowrie/

Task-02's replay log lands here: `cowrie.json`, JSON Lines (one event object per line, the same
shape `.claude/skills/cowrie-fixture/references/cowrie-events.md` documents), fully synthetic —
never a real attacker payload or a raw log copied off `hp-use-01`. It exercises the shipper
(task-02) end to end without a live honeypot host: tailing, session grouping on
`cowrie.session.closed`, and signed posting to the ingest endpoint.
