#!/usr/bin/env bash
# scripts/init_db.sh
# Инициализация БД для FinAssist (Postgres + Django)
#
# Behaviour:
#  - ждёт доступности Postgres
#  - создаёт роль (DB user) и базу, если не существуют
#  - (опционально) включает расширения citext, pgcrypto
#  - запускает Django миграции, collectstatic (опционально)
#  - создаёт Django superuser если заданы соответствующие env vars
#
# ENV (с примерами)
#  PGHOST=postgres
#  PGPORT=5432
#  PG_SUPERUSER=postgres
#  PG_SUPERUSER_PASSWORD=postgres
#
#  DB_NAME=finassist
#  DB_USER=finassist_user
#  DB_PASS=secret
#
#  ENABLE_EXTENSIONS=1           # 1/0 - включать citext/pgcrypto
#  RUN_MIGRATIONS=1              # 1/0 - запускать migrate
#  COLLECTSTATIC=0               # 1/0 - запускать collectstatic
#  INITIAL_FIXTURES=fixtures/initial_data.json  # optional, space-separated list
#
#  DJANGO_SUPERUSER_USERNAME=admin
#  DJANGO_SUPERUSER_EMAIL=admin@example.com
#  DJANGO_SUPERUSER_PASSWORD=supersecret
#
set -euo pipefail

# Defaults (can be overridden)
: "${PGHOST:=localhost}"
: "${PGPORT:=5432}"
: "${PG_SUPERUSER:=postgres}"
: "${PG_SUPERUSER_PASSWORD:=${POSTGRES_PASSWORD:-}}"

: "${DB_NAME:=finassist}"
: "${DB_USER:=finassist}"
: "${DB_PASS:=finassist_pass}"

: "${ENABLE_EXTENSIONS:=1}"
: "${RUN_MIGRATIONS:=1}"
: "${COLLECTSTATIC:=0}"
: "${MAX_WAIT:=60}"               # seconds to wait for Postgres
: "${SLEEP_INTERVAL:=2}"

# Django management (adjust path if needed)
DJANGO_MANAGE_PY_PATH=${DJANGO_MANAGE_PY_PATH:-./manage.py}

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

# Wait for postgres readiness
wait_for_postgres() {
  local elapsed=0
  log "Waiting for Postgres at ${PGHOST}:${PGPORT} (max ${MAX_WAIT}s)..."

  # If pg_isready exists, prefer it
  if command -v pg_isready >/dev/null 2>&1; then
    while ! pg_isready -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" >/dev/null 2>&1; do
      elapsed=$((elapsed + SLEEP_INTERVAL))
      if [ "${elapsed}" -ge "${MAX_WAIT}" ]; then
        log "Timed out waiting for Postgres."
        return 1
      fi
      sleep "${SLEEP_INTERVAL}"
    done
    log "Postgres is ready (pg_isready)."
    return 0
  fi

  # Fallback to attempting psql connection
  export PGPASSWORD="${PG_SUPERUSER_PASSWORD:-}"
  while ! psql -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -c '\q' >/dev/null 2>&1; do
    elapsed=$((elapsed + SLEEP_INTERVAL))
    if [ "${elapsed}" -ge "${MAX_WAIT}" ]; then
      log "Timed out waiting for Postgres (psql fallback)."
      return 1
    fi
    sleep "${SLEEP_INTERVAL}"
  done
  log "Postgres is ready (psql)."
  return 0
}

# Run SQL via psql as superuser (uses PGPASSWORD)
psql_super() {
  export PGPASSWORD="${PG_SUPERUSER_PASSWORD:-}"
  psql -v ON_ERROR_STOP=1 -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -c "$1"
}

# Create DB role and DB if missing
create_db_and_user() {
  log "Ensuring database role '${DB_USER}' exists..."
  export PGPASSWORD="${PG_SUPERUSER_PASSWORD:-}"
  local exists
  exists=$(psql -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}';" || echo "")
  if [ "${exists}" = "1" ]; then
    log "Role '${DB_USER}' already exists."
  else
    log "Creating role '${DB_USER}'..."
    # Note: single quotes in passwords may break SQL literal; avoid such characters or set password later
    psql -v ON_ERROR_STOP=1 -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -c "CREATE ROLE \"${DB_USER}\" WITH LOGIN PASSWORD '${DB_PASS}';"
    log "Role '${DB_USER}' created."
  fi

  log "Ensuring database '${DB_NAME}' exists..."
  exists=$(psql -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" || echo "")
  if [ "${exists}" = "1" ]; then
    log "Database '${DB_NAME}' already exists."
  else
    log "Creating database '${DB_NAME}' owned by '${DB_USER}'..."
    psql -v ON_ERROR_STOP=1 -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -c "CREATE DATABASE \"${DB_NAME}\" OWNER \"${DB_USER}\";"
    log "Database '${DB_NAME}' created."
  fi

  if [ "${ENABLE_EXTENSIONS}" = "1" ]; then
    log "Ensuring extensions (citext, pgcrypto) in '${DB_NAME}'..."
    export PGPASSWORD="${PG_SUPERUSER_PASSWORD:-}"
    psql -v ON_ERROR_STOP=1 -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS citext;" || log "Warning: can't create citext (maybe not allowed on managed DB)."
    psql -v ON_ERROR_STOP=1 -h "${PGHOST}" -p "${PGPORT}" -U "${PG_SUPERUSER}" -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" || log "Warning: can't create pgcrypto (maybe not allowed on managed DB)."
  fi
}

# Run Django manage commands (migrate, collectstatic, loaddata)
run_django_tasks() {
  if [ ! -f "${DJANGO_MANAGE_PY_PATH}" ]; then
    log "ERROR: ${DJANGO_MANAGE_PY_PATH} not found. Please run this script from project root or set DJANGO_MANAGE_PY_PATH."
    return 1
  fi

  # Set DATABASE_URL for Django if needed
  export DATABASE_URL="postgres://${DB_USER}:${DB_PASS}@${PGHOST}:${PGPORT}/${DB_NAME}"
  export DATABASE_NAME="${DB_NAME}"
  export DATABASE_USER="${DB_USER}"
  export DATABASE_PASSWORD="${DB_PASS}"
  export DATABASE_HOST="${PGHOST}"
  export DATABASE_PORT="${PGPORT}"

  # Run migrations
  if [ "${RUN_MIGRATIONS}" = "1" ]; then
    log "Running Django migrations..."
    # Try to run within current Python env
    if python -c "import django" >/dev/null 2>&1; then
      python "${DJANGO_MANAGE_PY_PATH}" migrate --noinput
    else
      log "Warning: Python environment does not have Django installed. Skipping migrate."
    fi
  else
    log "RUN_MIGRATIONS != 1 -> skipping migrate."
  fi

  # Collect static if requested
  if [ "${COLLECTSTATIC}" = "1" ]; then
    log "Collecting static files..."
    python "${DJANGO_MANAGE_PY_PATH}" collectstatic --noinput || log "collectstatic failed (continuing)."
  fi

  # Load fixtures if specified (space-separated list)
  if [ -n "${INITIAL_FIXTURES:-}" ]; then
    log "Loading fixtures: ${INITIAL_FIXTURES}"
    for f in ${INITIAL_FIXTURES}; do
      if [ -f "${f}" ]; then
        python "${DJANGO_MANAGE_PY_PATH}" loaddata "${f}" || log "Warning: loaddata failed for ${f}"
      else
        log "Fixture not found: ${f}"
      fi
    done
  fi
}

# Create Django superuser non-interactively if env vars set
create_django_superuser() {
  if [ -z "${DJANGO_SUPERUSER_USERNAME:-}" ] || [ -z "${DJANGO_SUPERUSER_EMAIL:-}" ] || [ -z "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    log "DJANGO_SUPERUSER_* not provided -> skipping superuser creation."
    return 0
  fi

  log "Creating Django superuser '${DJANGO_SUPERUSER_USERNAME}' if not exists..."
  python - <<PY
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', 'config.settings'))
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print("Superuser created:", username)
else:
    print("Superuser already exists:", username)
PY
}

main() {
  # 1) wait for postgres
  if ! wait_for_postgres; then
    log "Postgres not reachable - exiting."
    exit 2
  fi

  # 2) try create db & user if superuser credentials available
  if [ -n "${PG_SUPERUSER_PASSWORD:-}" ] || [ -n "${POSTGRES_PASSWORD:-}" ]; then
    create_db_and_user || log "Warning: create_db_and_user failed (maybe insufficient privileges)."
  else
    log "PG_SUPERUSER_PASSWORD/POSTGRES_PASSWORD not provided — пропускаем создание БД/ролей (ожидается, что БД уже настроена)."
  fi

  # 3) run Django tasks (migrate / collectstatic / fixtures)
  run_django_tasks

  # 4) create superuser if needed
  create_django_superuser

  log "DB init complete."
}

main "$@"
