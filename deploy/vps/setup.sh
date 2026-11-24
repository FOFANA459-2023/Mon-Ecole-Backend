#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu 24.04 VPS, run as root:
#   ssh -i ~/.ssh/mon-ecole-deploy root@<ip> 'bash -s' < deploy/vps/setup.sh
# Installs Docker, turns on automatic security updates, a firewall (SSH, HTTP, HTTPS) and 2 GB of swap.
# Safe to run again.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl ufw unattended-upgrades

if ! command -v docker > /dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
systemctl enable --now docker

# Containers' logs would otherwise grow without limit.
cat > /etc/docker/daemon.json <<'JSON'
{"log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "5"}}
JSON
systemctl restart docker

dpkg-reconfigure -f noninteractive unattended-upgrades

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

# Keys only: no password sign-in over SSH.
sed -ri 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh

if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

mkdir -p /opt/mon-ecole
echo "Server ready: $(docker --version), $(docker compose version)"
