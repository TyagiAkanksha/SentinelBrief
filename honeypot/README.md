# honeypot/ — Cowrie host runbook

Owner-run. This host runs exactly one thing: Cowrie in Docker, listening on the host's real port
22. Task-05 sequences these steps together with the app host's deploy. Nothing here touches AWS
by itself — every step below is a console/CLI action the owner performs by hand.

## 1. Instance

Amazon Linux 2023, `t4g.nano` (arm64) or `t3.nano` (x86_64 — both work; the Cowrie image is
multi-arch), in a **separate VPC** (or a separate AWS account — either satisfies PRD §10.4's
"assume it will be fully compromised, share nothing with the app host"), public subnet, Elastic
IP. Instance role: `AmazonSSMManagedInstanceCore` only — no other permissions, no access keys.

## 2. Security group

- Inbound: TCP 22 from `0.0.0.0/0` and `::/0` (Cowrie must be reachable from the whole internet —
  that is the point of a honeypot).
- Outbound: TCP 443 to `0.0.0.0/0` only (the ingest hostname the shipper posts to, and the
  SSM/EC2-messages endpoints the agent needs). Narrow this to VPC endpoints later if the owner
  adds them.
- Nothing else in either direction.

## 3. User data (pasted at instance launch)

Runs on the instance at first boot, via EC2 cloud-init — never on the operator's machine; it
cannot be executed here (implementer: syntax-checked with `bash -n`).

Paste the contents of `honeypot/user-data.sh` into the console's "User data" field at instance
launch. That file — not this README — is the source of truth (task-05 fix-1, review I3/M6): it is
`bash -n`'d by `tests/test_deploy_docs.py` and kept in sync with the notes below by hand.

Notes on each line (`honeypot/user-data.sh`):

- `#!/bin/bash` and `set -euo pipefail` — EC2 cloud-init only executes user data that starts with
  `#!` (a shell script) or `#cloud-config`; anything else is logged as unhandled non-multipart
  user data and never runs at all, which would leave a real `sshd` up on a security group that
  admits port 22 from `0.0.0.0/0`. `set -euo pipefail` stops the script on the first failure (a
  failed `curl` below, for example) instead of continuing into a half-built host.
- `dnf install -y docker python3.12` — AL2023's `docker` package ships no compose plugin
  (installed separately below); `python3.12` is task-02's shipper runtime.
- The compose plugin install is pinned to `v5.5.1` (verified against
  `gh api repos/docker/compose/releases/tags/v5.5.1` at the time this runbook was written;
  re-verify it is still the latest stable release before using it on a new host).
- `systemctl disable --now sshd && systemctl mask sshd` — port 22 must be free before Cowrie
  starts; masking prevents anything from re-enabling `sshd` later. SSM Agent is preinstalled on
  AL2023 and needs no port.
- `printf 'SystemMaxUse=200M\nStorage=persistent\n' >> /etc/systemd/journald.conf`, then
  `mkdir -p /var/log/journal && systemctl restart systemd-journald` — bounds the journal's
  on-disk size before Cowrie starts producing chatty traffic AND makes it persistent in place
  (task-05 fix-1, review M6: AL2023 ships `Storage=auto` with no `/var/log/journal`, i.e.
  volatile by default — without this, `SystemMaxUse=200M` bounds a journal that is wiped on every
  reboot) (`infra/deploy/database.md`'s "Logs" footnote observes this budget with `journalctl
  --disk-usage`).
- `mkdir -p /opt/sentinelbrief-honeypot/etc /opt/sentinelbrief-honeypot/data/{log,lib} && chown -R
  999:999 /opt/sentinelbrief-honeypot/data` — the `etc/` directory holds the copied
  `cowrie.cfg` (step 4); without it, Docker creates a root-owned directory at the missing bind
  source and Cowrie silently boots on `cowrie.cfg.dist` defaults, so every event carries the
  container hostname instead of `"sensor": "hp-use-01"` — a silent failure that would survive
  step 5's smoke unless the owner reads the `sensor` field. uid 999 is the `cowrie` user inside
  the official image (verified with `docker exec <container> python3 -c "import os;
  print(os.getuid())"` against the digest pinned in `honeypot/docker-compose.yml` — re-check this
  if the image is ever re-pinned, it is not guaranteed to stay 999 across releases); `data/{log,lib}`
  are the bind-mount sources `honeypot/docker-compose.yml` maps to `var/log/cowrie` and
  `var/lib/cowrie`.
- `useradd --system --no-create-home --shell /sbin/nologin shipper` — task-02's systemd unit runs
  as this unprivileged user, never root.

There is deliberately no `usermod -aG docker ssm-user` line (task-05 fix-1, review I3): AL2023's
SSM Agent creates the `ssm-user` account **lazily, at the first SSM session**, not at boot — a
`usermod` on a not-yet-existing user aborts the whole script under `set -euo pipefail`, line 2.
Every `docker compose` command in this runbook and in `infra/deploy/ec2-single-host.md` runs
inside an SSM session after `sudo -i`, so no group membership is needed.

## 4. Copy the compose files and pin the image digest

Copy `honeypot/docker-compose.yml` to `/opt/sentinelbrief-honeypot/docker-compose.yml` and
`honeypot/etc/cowrie.cfg` to `/opt/sentinelbrief-honeypot/etc/cowrie.cfg` (the compose file's
relative bind source `./etc/cowrie.cfg` resolves against the compose file's own directory, so the
cfg must land at exactly that path — not loose in `/opt/sentinelbrief-honeypot/`) via an SSM
Session Manager session (`aws ssm start-session --target <instance-id>`, then `sudo -i` — no
`usermod -aG docker` runs on this host (step 3), so every `docker`/`mkdir` command below needs
root, task-05 fix-2 review N2 — and a heredoc, **not** an S3 object as a courier: this host's
instance role, `AmazonSSMManagedInstanceCore` only, cannot read any bucket, task-05 fix-1 review
M5) — **never `scp`**, there is no `sshd` on this host to receive it.

Then, in the same SSM session:

```sh
docker pull cowrie/cowrie:latest
docker inspect --format '{{index .RepoDigests 0}}' cowrie/cowrie:latest
```

Compare the printed digest against the `image:` line already committed in
`honeypot/docker-compose.yml` (pinned in the repo as `cowrie/cowrie@sha256:<digest>` — see that
file's comment for when it was last pinned). If the digest has changed, edit the `image:` line to
the new digest **on the box and in the repo copy in the same sitting** — a moving tag is never
left on the box (the same drift rule as every other image in this repo).

## 5. Start Cowrie and verify

In the SSM session on the instance: guard the bind source before starting — a missing
`cowrie.cfg` becomes a root-owned directory and Cowrie boots silently on `.dist` defaults (step
4's note):

```sh
test -f /opt/sentinelbrief-honeypot/etc/cowrie.cfg
docker compose -f /opt/sentinelbrief-honeypot/docker-compose.yml up -d
```

From a laptop — runs against the deployed host, not here (`<elastic-ip>` does not exist until the
instance is launched):

```sh
ssh -p 22 root@<elastic-ip>
```

This should reach Cowrie's fake banner (`SSH-2.0-OpenSSH_6.0p1 Debian-4+deb7u2` by default); a
wrong password is accepted or rejected per Cowrie's userdb — either is Cowrie working correctly,
not a real `sshd`.

Back in the SSM session, verify from the HOST path, not through the container — the pinned Cowrie
image ships no shell and no coreutils (`docker compose exec cowrie tail …` fails with an OCI exec
error), and the host path is the same bind mount task-02's unprivileged `shipper` unit reads, so
this one command checks the events, the bind mount, and the shipper's read path together:

```sh
tail -n 3 /opt/sentinelbrief-honeypot/data/log/cowrie.json
```

This should show `cowrie.session.connect` … `cowrie.session.closed` events for the session just
opened, each carrying `"sensor": "hp-use-01"`.

## 6. What is NOT on this host

No repo checkout, no `.env` file, no database URL, no LLM API key, no AWS credentials beyond the
instance role (`AmazonSSMManagedInstanceCore`). The only secret ever placed on this host is the
shipper's `INGEST_HMAC_SECRET` (task-02, written to `/etc/sentinelbrief-shipper.env`, mode 600) —
its value is never written into this repo, this runbook, or any other file here.
