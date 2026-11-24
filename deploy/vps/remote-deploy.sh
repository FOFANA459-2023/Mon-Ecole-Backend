#!/usr/bin/env bash
# Runs on the VPS as root (sent over SSH by .github/scripts/deploy-vps.sh), from /opt/mon-ecole, after the
# workflow has copied compose.yaml, Caddyfile and app.env there and logged Docker in to GHCR:
#   1. pull the image and run the database migrations, stopping here if they fail (the old version keeps running);
#   2. restart the stack on the new image and wait until the API answers /healthz.
set -euo pipefail

image="${1:?usage: remote-deploy.sh <image> <api-host>}"
api_host="${2:?}"
cd /opt/mon-ecole
chmod 600 app.env
printf 'IMAGE=%s\nAPI_HOST=%s\n' "$image" "$api_host" > .env.next

echo "== Pull $image"
docker compose --env-file .env.next pull --quiet

echo "== Migrate"
docker compose --env-file .env.next run --rm --no-deps api python manage.py migrate --noinput

echo "== Restart"
mv .env.next .env
docker compose up -d --remove-orphans

for _ in $(seq 1 30); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" 2>/dev/null; then
    docker image prune -af --filter "until=168h" > /dev/null
    docker compose ps
    echo "Deployed $image on https://$api_host"
    exit 0
  fi
  sleep 3
done
docker compose logs --tail 80 api
echo "The API did not become healthy." >&2
exit 1
