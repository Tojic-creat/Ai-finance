"""
ASGI config for FinAssist project.

This module exposes the ASGI callable as a module-level variable named `application`.

ASGI используется:
- при необходимости async-эндпоинтов,
- для будущей поддержки WebSocket (уведомления, realtime-дашборд),
- при деплое через Uvicorn / Daphne / Hypercorn.

В MVP может не использоваться напрямую, но оставлен для расширяемости.
"""

import os

from django.core.asgi import get_asgi_application

# Устанавливаем настройки по умолчанию.
# Может быть переопределено через ENV (например, finassist.settings.prod)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "finassist.settings.dev")

# Создаём ASGI приложение Django
application = get_asgi_application()
