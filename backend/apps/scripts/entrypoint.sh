#!/bin/sh
set -e

# Default envs (if not set)
: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"
: "${DATABASE_URL:=postgresql://finassist:finassist_pass@${POSTGRES_HOST}:${POSTGRES_PORT}/finassist_dev}"
: "${DJANGO_COLLECTSTATIC:=1}"

echo "Entry point: environment summary:"
echo "POSTGRES_HOST=${POSTGRES_HOST}, POSTGRES_PORT=${POSTGRES_PORT}"
echo "DATABASE_URL=${DATABASE_URL}"
echo "DJANGO_COLLECTSTATIC=${DJANGO_COLLECTSTATIC}"

wait_for_db() {
  timeout=${DB_WAIT_TIMEOUT:-120}
  interval=${DB_WAIT_RETRY:-2}
  start_ts=$(date +%s)

  echo "Waiting for database to become available..."
  while true; do
    if python - <<'PY' 2>/dev/null
import os, sys, urllib.parse
try:
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit(1)
    parsed = urllib.parse.urlparse(url)
    conn_info = {
        "dbname": parsed.path.lstrip('/'),
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port
    }
    psycopg2.connect(**conn_info).close()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
    then
      echo "OK"
      return 0
    fi

    if command -v nc >/dev/null 2>&1; then
      nc -z "${POSTGRES_HOST}" "${POSTGRES_PORT}" >/dev/null 2>&1 && { echo "OK (tcp)"; return 0; }
    else
      (echo > /dev/tcp/"${POSTGRES_HOST}"/"${POSTGRES_PORT}") >/dev/null 2>&1 && { echo "OK (devtcp)"; return 0; }
    fi

    now_ts=$(date +%s)
    elapsed=$((now_ts - start_ts))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "Database didn't become available in time (timeout ${timeout}s)."
      return 1
    fi

    echo "DB not ready yet - sleeping ${interval}s..."
    sleep "${interval}"
  done
}

# Run wait
if ! wait_for_db; then
  echo "ERROR: Database not reachable. Exiting."
  exit 1
fi

# Apply migrations
echo "Apply database migrations..."
python manage.py migrate --noinput

# Collect static if requested
if [ "${DJANGO_COLLECTSTATIC}" = "1" ] || [ "${DJANGO_COLLECTSTATIC}" = "true" ]; then
  echo "Collect static files..."
  python manage.py collectstatic --noinput || true
fi

# Run any seed script (init_db.sh)
if [ -x ./scripts/init_db.sh ]; then
  echo "Running init_db.sh (seed)..."
  ./scripts/init_db.sh || echo "init_db.sh exited with non-zero code, continuing..."
fi

# --- DEBUG: show what args we got (so we can see if compose passed command) ---
echo "Entry point: args before exec: [$*] (count=$#)"

# If no args were supplied, fall back to a safe default for dev.
# This prevents the container from just exiting if compose didn't pass command.
if [ "$#" -eq 0 ]; then
  echo "No command provided to entrypoint — using default: python manage.py runserver 0.0.0.0:8000 --noreload"
  set -- python manage.py runserver 0.0.0.0:8000 --noreload
fi

# Finally, run the CMD (provided by dockerfile/compose)
exec "$@"
