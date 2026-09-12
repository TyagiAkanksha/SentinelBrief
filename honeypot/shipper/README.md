# `honeypot/shipper/` — the Cowrie log shipper

Owner-run, on the honeypot host only (task-05 sequences it, under an SSM Session Manager
session). Tails `cowrie.json`, groups events by Cowrie `session`, and POSTs one HMAC-signed
`SessionAlert` per `cowrie.session.closed` to SentinelBrief's ingest URL. Runs as the
unprivileged `shipper` system user (created by task-01's user data) under the hardened
`sentinelbrief-shipper.service` unit in this directory. Spools to local disk (write-before-POST)
when the ingest URL is unreachable, and drains the spool in order once it returns; a session
Cowrie never closes (a Cowrie restart) is flushed after an idle timeout.

## Install

`python3.12` is already installed by task-01's user data — nothing to do for it here:

```bash
dnf install -y python3.12
```
Not run here: AL2023-only (`dnf` does not exist on this dev machine), and it is a no-op on the
honeypot host itself — task-01's user data already installs it at instance boot.

Copy this directory to the host (via an SSM Session Manager session — never `scp`, there is no
`sshd` on this host) and build the venv:

```bash
sudo cp -r honeypot/shipper /opt/sentinelbrief-shipper/src
sudo python3.12 -m venv /opt/sentinelbrief-shipper/.venv
sudo /opt/sentinelbrief-shipper/.venv/bin/pip install /opt/sentinelbrief-shipper/src
```
Not run here: `/opt` on this machine is not the honeypot host, and this box has no non-interactive
`sudo`. The venv-build/`pip install` SHAPE was verified in this task's own scratchpad (a
non-privileged path standing in for `/opt/sentinelbrief-shipper`) — `python3 -m venv <scratch>
/m6-t02-venv && <scratch>/m6-t02-venv/bin/pip install honeypot/shipper` — and
`sentinelbrief-shipper --help` printed its usage line (pasted in the task-02 implementer report);
the two commands above are the identical `pip install <source dir>` invocation the wheel proof
already exercises, at the real host path.

Write the env file — root-owned, mode 600, exactly two lines, the secret pasted from the owner's
own terminal in the SSM session (never typed or stored anywhere else):

```bash
sudo install -m 600 -o root -g root /dev/null /etc/sentinelbrief-shipper.env
sudo tee /etc/sentinelbrief-shipper.env >/dev/null <<'EOF'
SHIPPER_INGEST_URL=https://api.sentinelbrief.tyagiakanksha.com/api/v1/alerts
INGEST_HMAC_SECRET=<pasted from the owner's terminal — never committed anywhere>
EOF
```
Not run here: writes a root-owned file with a real secret value — this must only ever happen in
the owner's own SSM session on the honeypot host, never in an agent transcript or this repo.

The env file above holds only the two required variables. Every other `SHIPPER_*` tunable
(`ShipperConfig`) takes its documented default unless also set in that same file:

| Variable | Default | Meaning |
|---|---|---|
| `SHIPPER_LOG_PATH` | `/opt/sentinelbrief-honeypot/data/log/cowrie.json` | The Cowrie JSON log to tail. |
| `SHIPPER_STATE_DIR` | `/var/lib/sentinelbrief-shipper` | Tail position, spool, and `spool/dead/`. |
| `SHIPPER_IDLE_FLUSH_S` | `900` | Ship a never-closed session after this many idle seconds. |
| `SHIPPER_MAX_EVENTS` | `2000` | Per-session event cap (floor 2 — below that no payload can ever validate). |
| `SHIPPER_MAX_PAYLOAD_BYTES` | `1500000` | Serialized payload byte cap; must stay below `INGEST_MAX_BODY_BYTES` and Caddy's 2 MB. |
| `SHIPPER_POST_TIMEOUT_S` | `10` | Per-POST HTTP timeout. |
| `SHIPPER_BACKOFF_BASE_S` | `2` | Delay before the first retry after a failed POST; doubles per consecutive failure. |
| `SHIPPER_BACKOFF_MAX_S` | `300` | Cap on the retry delay. |
| `SHIPPER_SPOOL_MAX_FILES` | `10000` | Disk-protection cap; the OLDEST spooled payload is dropped once exceeded. |
| `SHIPPER_POLL_INTERVAL_S` | `1` | Sleep between polls when the log has no new complete line. |

Install and start the unit:

```bash
cd /opt/sentinelbrief-shipper/src
sudo cp sentinelbrief-shipper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sentinelbrief-shipper
```
Not run here: this dev machine is the owner's own desktop, not the honeypot host — installing a
real systemd unit here would be installing it on the wrong machine entirely. `systemctl status
sentinelbrief-shipper` confirms it is running; `test_shipper_isolation.py
::test_unit_file_hardening_and_pyproject_shape` pins the unit file's hardening directives so a
typo here fails the test suite instead of only showing up on the box.

Verify delivery once the first attacker session closes:

```bash
journalctl -u sentinelbrief-shipper -f
```
Not run here: no `sentinelbrief-shipper` unit is installed on this machine (see above). On the
honeypot host this shows `shipper: delivered session_id=... status=202` lines — never a
username, password, command, banner, URL, or the `SHIPPER_INGEST_URL`/secret value (the shipper
never logs a payload field; `session_id` is the only per-session field it ever logs).

## What it never does

- Reads `honeypot/data/lib` (attacker-downloaded artifacts) — only `data/log/cowrie.json`.
- Logs a payload field: no username, password, command, banner, or URL — only `session_id` (a
  Cowrie-generated hex id, never attacker-controlled) and counters.
- Retries a `401`/`413`/`422` — those are permanent rejections, dead-lettered under
  `/var/lib/sentinelbrief-shipper/spool/dead/` for the owner to inspect by hand.
- Runs as root — the unit's `User=shipper`/`Group=shipper`, `NoNewPrivileges=true`,
  `ProtectSystem=strict` (read-only filesystem except its own `StateDirectory=`), `ProtectHome=true`.
- Holds any secret but `INGEST_HMAC_SECRET` — no database URL, no LLM key, no AWS credentials.
