#!/usr/bin/env bash
# Runs on the API server as root (sent through SSM by .github/scripts/deploy-ec2.sh), from /opt/mon-ecole:
#   1. fetch the app settings and API hostname from SSM Parameter Store;
#   2. pull the image and run the database migrations, stopping here if they fail (the old version keeps running);
#   3. restart the stack on the new image and wait until the API answers /healthz.
set -euo pipefail

image="${1:?usage: remote-deploy.sh <image> <environment> <region>}"
environment="${2:?}"
region="${3:?}"
cd /opt/mon-ecole

param() {
  aws ssm get-parameter --region "$region" --name "/mon-ecole/$environment/$1" --with-decryption \
    --query Parameter.Value --output text
}

echo "== Settings"
umask 077
param app-env > app.env.next
mv app.env.next app.env
api_host=$(param api-host)
printf 'IMAGE=%s\nAPI_HOST=%s\n' "$image" "$api_host" > .env.next

echo "== Pull $image"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "${image%%/*}"
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
