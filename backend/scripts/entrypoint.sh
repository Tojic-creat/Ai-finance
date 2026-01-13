#!/bin/sh
set -e

# Default envs (if not set)
: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"
: "${DATABASE_URL:=postgresql://finassist:finassist_pass@${POSTGRES_HOST}:${POSTGRES_PORT}/finassist_dev}"
: "${DJANGO_COLLECTSTATIC:=1}"
# STATIC_ROOT: can be set from Django settings via env; fallback to /app/staticfiles
: "${STATIC_ROOT:=/app/staticfiles}"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

log "Entry point: environment summary:"
log "POSTGRES_HOST=${POSTGRES_HOST}, POSTGRES_PORT=${POSTGRES_PORT}"
log "DATABASE_URL=${DATABASE_URL}"
log "DJANGO_COLLECTSTATIC=${DJANGO_COLLECTSTATIC}"
log "STATIC_ROOT=${STATIC_ROOT}"

wait_for_db() {
  timeout=${DB_WAIT_TIMEOUT:-120}
  interval=${DB_WAIT_RETRY:-2}
  start_ts=$(date +%s)

  log "Waiting for database to become available..."
  while true; do
    # Try to open a DB connection using psycopg2 if available (preferred)
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
      log "OK (psycopg2)"
      return 0
    fi

    # Fallback: test TCP port
    if command -v nc >/dev/null 2>&1; then
      if nc -z "${POSTGRES_HOST}" "${POSTGRES_PORT}" >/dev/null 2>&1; then
        log "OK (tcp nc)"
        return 0
      fi
    else
      # bash /dev/tcp trick (may not work in /bin/sh in some images)
      if (echo > /dev/tcp/"${POSTGRES_HOST}"/"${POSTGRES_PORT}") >/dev/null 2>&1; then
        log "OK (dev/tcp)"
        return 0
      fi
    fi

    now_ts=$(date +%s)
    elapsed=$((now_ts - start_ts))
    if [ "$elapsed" -ge "$timeout" ]; then
      log "Database didn't become available in time (timeout ${timeout}s)."
      return 1
    fi

    log "DB not ready yet - sleeping ${interval}s..."
    sleep "${interval}"
  done
}

# Ensure STATIC_ROOT exists and is writable before collectstatic runs.
if [ ! -d "${STATIC_ROOT}" ]; then
  log "Creating STATIC_ROOT directory: ${STATIC_ROOT}"
  mkdir -p "${STATIC_ROOT}"
fi
# permissive but safe: readable & writable by container user
chmod 0755 "${STATIC_ROOT}" || true

# Run wait
if ! wait_for_db; then
  log "ERROR: Database not reachable. Exiting."
  exit 1
fi

# Apply migrations
log "Apply database migrations..."
python manage.py migrate --noinput

# Collect static if requested
if [ "${DJANGO_COLLECTSTATIC}" = "1" ] || [ "${DJANGO_COLLECTSTATIC}" = "true" ]; then
  log "Collect static files..."
  # Ensure the directory still exists (in case settings uses different path)
  mkdir -p "${STATIC_ROOT}" || true
  chmod 0755 "${STATIC_ROOT}" || true

  # Run collectstatic; if it fails we print an error and continue (for dev). 
  # Remove '|| true' if you prefer the container to fail on collectstatic error.
  if ! python manage.py collectstatic --noinput; then
    log "collectstatic failed — continuing (you may want to check STATIC_ROOT and file permissions)."
  fi
fi

# Run any seed script (init_db.sh) at known absolute path
if [ -x /app/scripts/init_db.sh ]; then
  log "Running /app/scripts/init_db.sh (seed)..."
  /app/scripts/init_db.sh || log "init_db.sh exited with non-zero code, continuing..."
else
  log "/app/scripts/init_db.sh not found or not executable; skipping seed."
fi

# --- DEBUG: show what args we got (so we can see if compose passed command) ---
log "Entry point: args before exec: [$*] (count=$#)"

# If no args were supplied, fall back to a safe default for dev.
if [ "$#" -eq 0 ]; then
  log "No command provided to entrypoint — using default: python manage.py runserver 0.0.0.0:8000 --noreload"
  set -- python manage.py runserver 0.0.0.0:8000 --noreload
fi

# Finally, run the CMD (provided by dockerfile/compose)
exec "$@"