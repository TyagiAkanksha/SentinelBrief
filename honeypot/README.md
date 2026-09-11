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

## 3. User data (pasted verbatim at instance launch)

```sh
dnf install -y docker python3.12
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-$(uname -m) -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
systemctl disable --now sshd && systemctl mask sshd
mkdir -p /opt/sentinelbrief-honeypot/data/{log,lib} && chown -R 999:999 /opt/sentinelbrief-honeypot/data
useradd --system --no-create-home --shell /sbin/nologin shipper
usermod -aG docker ssm-user
```

Notes on each line:

- `dnf install -y docker python3.12` — AL2023's `docker` package ships no compose plugin
  (installed separately below); `python3.12` is task-02's shipper runtime.
- The compose plugin install is pinned to `v5.5.1` (verified against
  `gh api repos/docker/compose/releases/tags/v5.5.1` at the time this runbook was written;
  re-verify it is still the latest stable release before using it on a new host).
- `systemctl disable --now sshd && systemctl mask sshd` — port 22 must be free before Cowrie
  starts; masking prevents anything from re-enabling `sshd` later. SSM Agent is preinstalled on
  AL2023 and needs no port.
- `mkdir -p /opt/sentinelbrief-honeypot/data/{log,lib} && chown -R 999:999
  /opt/sentinelbrief-honeypot/data` — uid 999 is the `cowrie` user inside the official image
  (verified with `docker exec <container> python3 -c "import os; print(os.getuid())"` against the
  digest pinned in `honeypot/docker-compose.yml` — re-check this if the image is ever re-pinned,
  it is not guaranteed to stay 999 across releases); these two directories are the bind-mount
  sources `honeypot/docker-compose.yml` maps to `var/log/cowrie` and `var/lib/cowrie`.
- `useradd --system --no-create-home --shell /sbin/nologin shipper` — task-02's systemd unit runs
  as this unprivileged user, never root.
- `usermod -aG docker ssm-user` — lets an SSM Session Manager session run `docker compose`
  commands without `sudo`.

## 4. Copy the compose files and pin the image digest

Copy `honeypot/docker-compose.yml` and `honeypot/etc/cowrie.cfg` to
`/opt/sentinelbrief-honeypot/` via an SSM Session Manager session (`aws ssm start-session` and a
heredoc, or an S3 object as a courier) — **never `scp`**, there is no `sshd` on this host to
receive it.

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

```sh
docker compose -f /opt/sentinelbrief-honeypot/docker-compose.yml up -d
```

From a laptop:

```sh
ssh -p 22 root@<elastic-ip>
```

This should reach Cowrie's fake banner (`SSH-2.0-OpenSSH_6.0p1 Debian-4+deb7u2` by default); a
wrong password is accepted or rejected per Cowrie's userdb — either is Cowrie working correctly,
not a real `sshd`.

Back in the SSM session:

```sh
docker compose -f /opt/sentinelbrief-honeypot/docker-compose.yml exec cowrie \
  tail -n 3 /cowrie/cowrie-git/var/log/cowrie/cowrie.json
```

This should show `cowrie.session.connect` … `cowrie.session.closed` events for the session just
opened, each carrying `"sensor": "hp-use-01"`.

## 6. What is NOT on this host

No repo checkout, no `.env` file, no database URL, no LLM API key, no AWS credentials beyond the
instance role (`AmazonSSMManagedInstanceCore`). The only secret ever placed on this host is the
shipper's `INGEST_HMAC_SECRET` (task-02, written to `/etc/sentinelbrief-shipper.env`, mode 600) —
its value is never written into this repo, this runbook, or any other file here.
