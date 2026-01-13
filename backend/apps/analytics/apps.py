# backend/apps/analytics/apps.py
from __future__ import annotations

import logging
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class AnalyticsConfig(AppConfig):
    """
    AppConfig for the `analytics` app.

    Responsibilities:
    - Provide a human-friendly verbose_name shown in admin.
    - Import signal handlers and task modules when the app is ready so they are registered.
    - Be defensive: missing optional modules should not break project startup.
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
    verbose_name = _("Analytics")

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
                "AnalyticsConfig.ready: signals import failed or not present (%s)", exc)

        try:
            # Import tasks for Celery autodiscovery if present
            from . import tasks  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "AnalyticsConfig.ready: tasks import failed or not present (%s)", exc)

        # Optional startup checks / registrations
        try:
            from django.conf import settings  # lazy import
            # Example: ensure an expected setting exists; do not raise if absent
            if not hasattr(settings, "ANALYTICS_ENABLED"):
                logger.debug(
                    "AnalyticsConfig.ready: ANALYTICS_ENABLED setting not found; defaulting to False")
        except Exception:
            # Non-fatal; only informational
            pass
