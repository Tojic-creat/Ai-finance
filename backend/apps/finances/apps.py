# backend/apps/finances/apps.py
from __future__ import annotations

import logging
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


logger = logging.getLogger(__name__)


class FinancesConfig(AppConfig):
    """
    AppConfig for the `finances` app.

    Responsibilities:
    - Provide a human-friendly verbose_name shown in admin.
    - Ensure signal handlers and Celery tasks are imported when the app is ready.
    - Fail gracefully (log warnings) if optional modules are missing during early development.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finances"
    verbose_name = _("Finances")

    def ready(self) -> None:
        """
        Called when Django starts. Import signals/tasks here so handlers and Celery tasks are registered.

        We catch and log exceptions to avoid breaking startup when signals/tasks are not implemented yet.
        """
        # Import signals if present
        try:
            # Local import to avoid circular imports at module import time
            from . import signals  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "FinancesConfig.ready: signals import failed or not present (%s)", exc)

        # Import tasks (for Celery autodiscovery) if present
        try:
            from . import tasks  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "FinancesConfig.ready: tasks import failed or not present (%s)", exc)

        # Optionally register any runtime checks or startup validations here
        try:
            # For example: ensure required settings or feature flags exist
            from django.conf import settings  # imported lazily
            if not hasattr(settings, "MAX_ATTACHMENT_SIZE"):
                logger.debug(
                    "FinancesConfig.ready: MAX_ATTACHMENT_SIZE not set; using default in settings.base")
        except Exception:
            # Non-fatal; only informational
            pass
