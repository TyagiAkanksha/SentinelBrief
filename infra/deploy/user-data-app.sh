#!/bin/bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-$(uname -m) -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
systemctl disable --now sshd && systemctl mask sshd
mkdir -p /var/backups/sentinelbrief
install -d -o 1001 -g 1001 -m 755 /opt/sentinelbrief/geoip
printf 'SystemMaxUse=500M\nStorage=persistent\n' >> /etc/systemd/journald.conf
mkdir -p /var/log/journal
systemctl restart systemd-journald
