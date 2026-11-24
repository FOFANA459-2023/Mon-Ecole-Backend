#!/usr/bin/env bash
# Deploy one image to the VPS (deploy/vps), used by .github/workflows/deploy.yml: copy deploy/vps and the app
# settings to the server over SSH, log the server in to GHCR for this pull only, then run remote-deploy.sh.
set -euo pipefail

image="${1:?usage: deploy-vps.sh <image>}"
: "${VPS_HOST:?}" "${VPS_SSH_KEY:?}" "${VPS_KNOWN_HOSTS:?}" "${API_HOST:?}" "${APP_ENV:?}" "${GHCR_TOKEN:?}"
user="${VPS_USER:-root}"

mkdir -p ~/.ssh
chmod 700 ~/.ssh
printf '%s\n' "$VPS_SSH_KEY" > ~/.ssh/deploy_key
printf '%s\n' "$VPS_KNOWN_HOSTS" > ~/.ssh/known_hosts
chmod 600 ~/.ssh/deploy_key ~/.ssh/known_hosts
ssh_opts=(-i ~/.ssh/deploy_key -o StrictHostKeyChecking=yes -o BatchMode=yes)
target="$user@$VPS_HOST"

ssh "${ssh_opts[@]}" "$target" 'mkdir -p /opt/mon-ecole'
scp "${ssh_opts[@]}" deploy/vps/compose.yaml deploy/vps/Caddyfile deploy/vps/remote-deploy.sh "$target:/opt/mon-ecole/"
# Settings go through stdin, never the command line.
printf '%s\n' "$APP_ENV" | ssh "${ssh_opts[@]}" "$target" 'umask 077; cat > /opt/mon-ecole/app.env'
printf '%s' "$GHCR_TOKEN" | ssh "${ssh_opts[@]}" "$target" \
  "docker login ghcr.io --username '$GITHUB_ACTOR' --password-stdin > /dev/null"

status=0
ssh "${ssh_opts[@]}" "$target" "bash /opt/mon-ecole/remote-deploy.sh '$image' '$API_HOST'" || status=$?
ssh "${ssh_opts[@]}" "$target" 'docker logout ghcr.io > /dev/null' || true

if [[ $status -ne 0 ]]; then
  echo "::error::Deploy failed. The previous version is still running unless the restart step was reached."
  exit "$status"
fi
