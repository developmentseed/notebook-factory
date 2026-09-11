#!/usr/bin/env bash
set -euo pipefail
cd /app

wait_for_db() {
  python - <<'PY'
import os, time, sys
import psycopg
url = os.environ.get("DATABASE_URL", "")
url = url.replace("postgis://", "postgresql://")
for i in range(60):
    try:
        psycopg.connect(url, connect_timeout=3).close(); sys.exit(0)
    except Exception as e:
        time.sleep(2)
print("database not reachable", file=sys.stderr); sys.exit(1)
PY
}

case "${1:-web}" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    python manage.py sync_templates
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-3}" --timeout 120
    ;;
  dev)
    wait_for_db
    python manage.py migrate --noinput
    python manage.py sync_templates
    exec python manage.py runserver 0.0.0.0:8000
    ;;
  worker)
    wait_for_db
    exec celery -A config worker -l info --concurrency "${CELERY_CONCURRENCY:-2}" -Q celery
    ;;
  beat)
    wait_for_db
    exec celery -A config beat -l info --schedule /app/data/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
