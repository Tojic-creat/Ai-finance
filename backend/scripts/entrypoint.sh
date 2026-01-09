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

# Wait for DB to be reachable (use python+psycopg2 if available; fallback to tcp socket test)
wait_for_db() {
  local timeout=${DB_WAIT_TIMEOUT:-120}
  local interval=${DB_WAIT_RETRY:-2}
  local start_ts=$(date +%s)

  echo "Waiting for database to become available..."
  while true; do
    # Try python + psycopg2 connection (most reliable)
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

    # Fallback: try to open TCP socket to host:port
    if command -v nc >/dev/null 2>&1; then
      nc -z "${POSTGRES_HOST}" "${POSTGRES_PORT}" >/dev/null 2>&1 && { echo "OK (tcp)"; return 0; }
    else
      # busybox style? try /dev/tcp
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

# Finally, run the CMD (provided by dockerfile/compose)
exec "$@"
