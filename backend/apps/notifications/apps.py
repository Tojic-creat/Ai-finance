# backend/apps/notifications/apps.py
from __future__ import annotations

import logging
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class NotificationsConfig(AppConfig):
    """
    AppConfig for the `notifications` app.

    Responsibilities:
    - Provide a human-friendly verbose_name shown in admin.
    - Import signal handlers and Celery task modules when the app is ready so they are registered.
    - Be defensive: missing optional modules should not break project startup.
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    verbose_name = _("Notifications")

    def ready(self) -> None:
        """
        Called when Django starts. Import signals/tasks here so handlers and Celery tasks are registered.

        We catch and log exceptions to avoid breaking startup when optional modules are missing
        during early development.
        """
        try:
            # Import signals (if present) so they are connected
            from . import signals  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "NotificationsConfig.ready: signals import failed or not present (%s)", exc)

        try:
            # Import tasks for Celery autodiscovery if present
            from . import tasks  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "NotificationsConfig.ready: tasks import failed or not present (%s)", exc)

        # Optional sanity checks / info logs
        try:
            from django.conf import settings  # lazy import
            if not hasattr(settings, "NOTIFICATIONS_ENABLED"):
                logger.debug(
                    "NotificationsConfig.ready: NOTIFICATIONS_ENABLED not set; defaulting to False (no sending will occur)")
        except Exception:
            # Non-fatal: just log
            logger.debug(
                "NotificationsConfig.ready: could not read settings for notifications")
