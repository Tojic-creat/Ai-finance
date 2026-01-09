# backend/finassist/celery.py
import os
from celery import Celery

# Убедимся, что настройки Django выставлены (используйте dev/ prod по окружению)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "finassist.settings.dev"))

# Создаём Celery приложение и называем его 'celery' (именно это имя ожидает сейчас Docker compose)
celery = Celery("finassist")

# Конфигурируем Celery из настроек Django: префикс CELERY_*
celery.config_from_object("django.conf:settings", namespace="CELERY")

# Автопоиск tasks.py в установленных приложениях
celery.autodiscover_tasks()
