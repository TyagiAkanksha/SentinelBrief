#!/bin/bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v5.5.1/docker-compose-linux-$(uname -m) -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
systemctl disable --now sshd && systemctl mask sshd
mkdir -p /opt/sentinelbrief/geoip /var/backups/sentinelbrief
usermod -aG docker ssm-user
echo 'SystemMaxUse=500M' >> /etc/systemd/journald.conf
echo 'Storage=persistent' >> /etc/systemd/journald.conf
systemctl restart systemd-journald
