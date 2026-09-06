# M0 fixture alerts

Five synthetic Cowrie session alerts (PRD §1.2 envelope), one per PRD §6.6 severity band, authored
with the `cowrie-fixture` skill. Each is a complete session: every event shares the envelope's
`session_id`, events are in timestamp order starting with `cowrie.session.connect`, and the
session ends with a `cowrie.session.closed` event carrying `duration_ms`.

These fixtures carry **no labels** — golden-set labels (`{severity, category, escalate}`) are
authored in M1 against `evals/golden/v1.jsonl`, not here. The "intended band" column below is the
rationale for *why* each fixture was written the way it was, per the PRD §6.6 rubric; it is not a
machine-checked assertion.

| File | Scenario | Intended §6.6 band |
|---|---|---|
| `alert1.json` | Single SSH connect, client banner, disconnect after ~2s. No login attempt at all. | 1 — background noise (mass-scanner-style probe) |
| `alert2.json` | Ten `login.failed` attempts using generic default creds (`root/123456`, `admin/admin`, `guest/guest`, ...), no success. | 2 — untargeted automation (generic credential spraying) |
| `alert3.json` | Forty `login.failed` attempts whose usernames reference the sensor hostname `hp-sgp-01` (`hp-sgp-01`, `sgp01admin`, `honeypot`, `sgp-01`, ...), no success. | 3 — engaged attacker, no foothold (high-volume, host-targeted brute force) |
| `alert4.json` | `login.success` followed by recon commands (`uname -a`, `cat /etc/passwd`, `w`). No download, no persistence. | 4 — foothold achieved |
| `alert5.json` | `login.success`, a `wget` to a payload URL, a `cowrie.session.file_download` event, then a `crontab` persistence command. | 5 — active compromise behavior (malware download + persistence) |

Documentation-range IPs only (`203.0.113.0/24`, `198.51.100.0/24`, `192.0.2.0/24`); no real
attacker data. Assumption carried from the skill: synthetic event shapes are re-checked against a
real Cowrie log at M6.
