#!/bin/bash
set -euo pipefail
dnf install -y docker python3.12
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-$(uname -m) -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
systemctl disable --now sshd && systemctl mask sshd
printf 'SystemMaxUse=200M\nStorage=persistent\n' >> /etc/systemd/journald.conf
mkdir -p /var/log/journal
systemctl restart systemd-journald
mkdir -p /opt/sentinelbrief-honeypot/etc /opt/sentinelbrief-honeypot/data/{log,lib} && chown -R 999:999 /opt/sentinelbrief-honeypot/data
useradd --system --no-create-home --shell /sbin/nologin shipper
