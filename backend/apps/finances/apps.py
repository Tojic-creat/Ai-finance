# backend/apps/finances/apps.py
from __future__ import annotations

import importlib
import logging
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class FinancesConfig(AppConfig):
    """
    AppConfig for the `finances` app.

    Responsibilities:
    - Human-friendly verbose_name.
    - Import signals/tasks when app is ready (register handlers).
    - Fail gracefully (log) when optional modules are missing.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finances"
    verbose_name = _("Finances")

    def ready(self) -> None:
        # Import celery tasks (optional) for autodiscovery
        try:
            importlib.import_module("apps.finances.tasks")
            logger.debug("FinancesConfig.ready: imported apps.finances.tasks")
        except ModuleNotFoundError:
            logger.debug("FinancesConfig.ready: apps.finances.tasks not found (optional)")
        except Exception:
            logger.exception("FinancesConfig.ready: error importing apps.finances.tasks (continuing)")

        # Import and register signals if present
        try:
            mod = importlib.import_module("apps.finances.signals")
            # Prefer an explicit registration function if available
            if hasattr(mod, "register_signals"):
                try:
                    mod.register_signals()
                    logger.debug("FinancesConfig.ready: registered signals from apps.finances.signals")
                except Exception:
                    logger.exception("FinancesConfig.ready: register_signals() failed")
            else:
                # If signals module uses decorator-based receivers, importing it is enough
                logger.debug("FinancesConfig.ready: imported apps.finances.signals (no register_signals found)")
        except ModuleNotFoundError:
            logger.debug("FinancesConfig.ready: apps.finances.signals not found (optional)")
        except Exception:
            logger.exception("FinancesConfig.ready: error importing apps.finances.signals (continuing)")

        # Optional runtime checks / info
        try:
            from django.conf import settings  # lazy import
            if not hasattr(settings, "MAX_ATTACHMENT_SIZE"):
                logger.debug("FinancesConfig.ready: MAX_ATTACHMENT_SIZE not configured; default will be used")
        except Exception:
            # non-fatal
            pass
