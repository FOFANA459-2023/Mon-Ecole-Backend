#!/bin/bash
# First boot of the API server (Amazon Linux 2023, arm64): Docker + Compose, swap, log rotation.
# The app itself arrives with the first deploy (deploy/ec2, sent through SSM by GitHub Actions).
set -euxo pipefail

dnf install -y docker
mkdir -p /usr/libexec/docker/cli-plugins
curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose \
  "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64"
chmod +x /usr/libexec/docker/cli-plugins/docker-compose

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" } }
EOF
systemctl enable --now docker

# 2 GB of swap: headroom for image pulls and migrations next to the running app.
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon -a

mkdir -p /opt/mon-ecole
chmod 750 /opt/mon-ecole
