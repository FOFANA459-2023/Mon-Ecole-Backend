#!/usr/bin/env bash
# Temporary test mode on the API server: run the app on a separate database of fictional schools, so the
# real database (Supabase) stays untouched. Run as root from /opt/mon-ecole (Session Manager, or SSM).
#
#   demo-db.sh on       start the test database, migrate it, fill it once (seed_showcase), switch the app to it
#   demo-db.sh off      switch the app back to the real database (the test database is kept, stopped)
#   demo-db.sh status   which database the app is using
#   demo-db.sh destroy  'off', then delete the test database and its data for good
#
# A deploy always restarts the app on the real database; run 'on' again afterwards to keep testing.
set -euo pipefail
cd /opt/mon-ecole

demo=(docker compose -f compose.yaml -f compose.demo.yaml)
real=(docker compose -f compose.yaml)

wait_healthy() {
  for _ in $(seq 1 40); do
    if docker compose exec -T api python -c \
      "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" 2>/dev/null; then
      return 0
    fi
    sleep 3
  done
  echo "The API did not become healthy." >&2
  return 1
}

case "${1:-status}" in
  on)
    "${demo[@]}" up -d --wait demo-db
    "${demo[@]}" run --rm --no-deps api python manage.py migrate --noinput
    if [ "$("${demo[@]}" run --rm --no-deps api python manage.py shell -c \
      "from apps.schools.models import School; print(School.objects.filter(code__startswith='essai-').count())" \
      2>/dev/null | tail -1)" = "0" ]; then
      echo "== Filling the test database (a few minutes)"
      "${demo[@]}" run --rm --no-deps api python manage.py seed_showcase --allow-production
    fi
    "${demo[@]}" up -d
    wait_healthy
    echo "The app now runs on the TEST database. Real data is untouched. Back: demo-db.sh off"
    ;;
  off)
    "${real[@]}" up -d --remove-orphans
    wait_healthy
    echo "The app runs on the REAL database again (test database kept; 'destroy' deletes it)."
    ;;
  status)
    url=$(docker compose exec -T api printenv DATABASE_URL 2>/dev/null || true)
    case "$url" in
      *@demo-db:*) echo "TEST database (fictional schools)" ;;
      "") echo "API not running" ;;
      *) echo "REAL database" ;;
    esac
    ;;
  destroy)
    "${real[@]}" up -d --remove-orphans
    wait_healthy
    docker volume rm mon-ecole_demo-pgdata 2>/dev/null || true
    echo "Test database deleted. The app runs on the real database."
    ;;
  *)
    echo "usage: demo-db.sh on|off|status|destroy" >&2
    exit 2
    ;;
esac
