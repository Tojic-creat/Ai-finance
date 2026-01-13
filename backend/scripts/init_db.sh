#!/bin/sh
set -e

# Defaults — можно переопределить через env
: "${DJANGO_SUPERUSER_EMAIL:=admin@example.com}"
: "${DJANGO_SUPERUSER_USERNAME:=admin}"
: "${DJANGO_SUPERUSER_PASSWORD:=adminpass}"

echo "Init DB: creating default data if needed (via manage.py shell)..."

# Run an embedded Python script (heredoc). We read env-vars from os.environ inside Python to avoid
# shell quoting/escaping problems when passing values into one-liners.
python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "adminpass")

try:
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username, email, password)
        print("Superuser created")
    else:
        print("Superuser already exists")
except Exception as exc:
    # If something goes wrong (e.g. DB not ready), print message and exit non-zero so caller can notice.
    # This preserves behaviour of set -e in the shell: container startup will log the error.
    import sys
    print("Error while creating superuser:", exc, file=sys.stderr)
    raise
PY

# здесь можно добавить другие idempotent seed команды:
# python manage.py loaddata fixtures/default_categories.json || true

echo "Init DB: done."
