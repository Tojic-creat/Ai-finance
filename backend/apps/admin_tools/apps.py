# backend/apps/admin_tools/apps.py
"""
AppConfig for the admin_tools helper app.

This module keeps startup logic minimal and defensive:
- sets a friendly verbose_name
- on ready() it attempts to perform safe, lazy imports of admin helpers
  so they are registered with Django admin if available.
- avoids heavy imports during import-time to keep manage.py/test fast.
"""
from __future__ import annotations

import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class AdminToolsConfig(AppConfig):
    name = "apps.admin_tools"
    verbose_name = "Admin tools (helpers)"
    default_auto_field = "django.db.models.AutoField"

    def ready(self) -> None:
        """
        Called when Django starts. Do tiny, safe initialization:

        - Try to import admin helper modules so admin actions / ModelAdmin
          classes register automatically in the project's admin site.
        - Any exception is logged but does not block startup.
        """
        # Avoid running heavy initialization during certain Django management commands
        # (migrations, collectstatic, tests may import apps but we keep ready lightweight).
        try:
            # Only import admin helpers if admin is enabled in INSTALLED_APPS
            if "django.contrib.admin" in settings.INSTALLED_APPS:
                # Lazy import admin helpers - they register ModelAdmin classes when imported.
                # Use importlib to avoid hard failure on missing modules.
                import importlib

                try:
                    importlib.import_module("apps.admin_tools.admin")  # noqa: F401
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug(
                        "admin_tools: optional admin helpers could not be imported: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("admin_tools AppConfig.ready failed: %s", exc)
