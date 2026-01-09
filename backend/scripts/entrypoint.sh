#!/usr/bin/env bash
set -e

# простой wait-for-db (проверка соединения)
echo "Waiting for database to become available..."
MAX_RETRIES=30
i=0
while ! python - <<PYCODE
import sys, os, psycopg2
try:
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
    conn.close()
    print('OK')
    sys.exit(0)
except Exception as e:
    # print(e)
    sys.exit(1)
PYCODE
do
  i=$((i+1))
  if [ "$i" -ge "$MAX_RETRIES" ]; then
    echo "Database didn't become available in time (timeout)."
    exit 1
  fi
  echo "DB not ready yet - sleeping 2s (attempt $i/$MAX_RETRIES)..."
  sleep 2
done

echo "Database is available."

# Выполнить миграции
echo "Apply database migrations..."
python manage.py migrate --noinput

# Собрать статические (в dev можно отключить)
if [ "${DJANGO_COLLECTSTATIC:-1}" != "0" ]; then
  echo "Collect static files..."
  python manage.py collectstatic --noinput
fi

# Применить любые seed-скрипты (если есть)
if [ -f ./scripts/init_db.sh ]; then
  echo "Running init_db.sh (seed)..."
  ./scripts/init_db.sh || true
fi

# Запустить Gunicorn (production) или runserver (dev)
if [ "${DJANGO_ENV:-dev}" = "prod" ]; then
  echo "Starting gunicorn..."
  gunicorn finassist.wsgi:application --bind 0.0.0.0:8000 --workers 3
else
  echo "Starting Django development server..."
  # используем 0.0.0.0 чтобы быть доступным из контейнера
  python manage.py runserver 0.0.0.0:8000
fi
