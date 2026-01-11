#!/usr/bin/env python
"""Django's command-line utility for administrative tasks.

This manage.py is tailored for the FinAssist project (backend/finassist).
It:
 - loads environment variables from a .env file (if present),
 - sets a sensible default DJANGO_SETTINGS_MODULE (finassist.settings.dev),
   but allows overriding via the environment (e.g. DJANGO_SETTINGS_MODULE=finassist.settings.prod),
 - then delegates to Django's command machinery.
"""

import os
import sys
from pathlib import Path

# --- Опционально подгружаем .env (если присутствует) ---
# .env может находиться в корне репозитория или в папке backend/.
# Это удобно для локальной разработки; в контейнерах обычно используются ENV переменные.
try:
    # python-dotenv (installed via requirements)
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# repo root (one level above backend/)
BASE_DIR = Path(__file__).resolve().parent.parent
# Попробуем несколько стандартных мест для .env
_dotenv_candidates = [
    BASE_DIR / ".env",
    Path(__file__).resolve().parent / ".env",  # backend/.env
]

if load_dotenv is not None:
    for p in _dotenv_candidates:
        if p.exists():
            load_dotenv(dotenv_path=str(p))
            # don't print in CI; only helpful in local dev
            if os.environ.get("CI", "").lower() not in ("1", "true"):
                print(f"Loaded environment from {p}")
            break


def main():
    # Установим значение DJANGO_SETTINGS_MODULE по умолчанию, если оно не задано.
    # Для разработки по умолчанию используем finassist.settings.dev — удобно для local docker-compose.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "finassist.settings.dev")

    # Позволяем переопределять DJANGO_SETTINGS_MODULE извне (например, в CI/CD / Docker)
    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE")
    if settings_module is None:
        # safety: ensure it's set
        os.environ["DJANGO_SETTINGS_MODULE"] = "finassist.settings.dev"
        settings_module = "finassist.settings.dev"

    # Небольшая полезная диагностика при запуске вручную
    if os.environ.get("CI", "").lower() not in ("1", "true"):
        print(f"Using settings module: {settings_module}")

    try:
        # Импортируем Django и делегируем управление
        from django.core.management import execute_from_command_line
    except Exception as exc:
        # Provide a more informative error if Django is not installed / not importable
        raise RuntimeError(
            "Failed to import Django. Is it installed and available on PYTHONPATH? "
            "Did you forget to activate a virtualenv? Original error: "
        ) from exc

    # Запуск команды
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
