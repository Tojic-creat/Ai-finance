#!/bin/sh
set -e

# Defaults — можно переопределить через env
: "${DJANGO_SUPERUSER_EMAIL:=admin@example.com}"
: "${DJANGO_SUPERUSER_USERNAME:=admin}"
: "${DJANGO_SUPERUSER_PASSWORD:=adminpass}"

echo "Init DB: creating default data if needed (via manage.py shell)..."

# create superuser idempotently via manage.py shell
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); \
if not User.objects.filter(username='${DJANGO_SUPERUSER_USERNAME}').exists(): \
    User.objects.create_superuser('${DJANGO_SUPERUSER_USERNAME}', '${DJANGO_SUPERUSER_EMAIL}', '${DJANGO_SUPERUSER_PASSWORD}'); \
    print('Superuser created'); \
else: \
    print('Superuser already exists')"

# здесь можно добавить другие idempotent seed команды:
# python manage.py loaddata fixtures/default_categories.json || true

echo "Init DB: done."
