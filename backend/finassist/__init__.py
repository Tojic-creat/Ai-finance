# Экспортируем celery app как атрибут пакета, чтобы `-A finassist` работал.
from .celery import celery  # noqa: F401