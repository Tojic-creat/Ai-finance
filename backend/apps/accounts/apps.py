# backend/apps/accounts/apps.py
from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    """
    Django AppConfig for the `accounts` app.

    Responsibilities:
    - Provide a human-friendly verbose_name shown in admin.
    - Ensure signals are imported when the app is ready (so handlers are registered).
    - Keep configuration minimal and safe (import errors in signals are caught and logged).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = _("Accounts")

    def ready(self) -> None:
        """
        Called when Django starts. Import signal handlers here so they are registered.

        If signals.py is not implemented yet or raises errors during import, we catch
        exceptions to avoid breaking the whole project startup. During development
        it's often helpful to let import errors surface, but in production we prefer
        a graceful fallback (log and continue).
        """
        # Import signals module to register signal handlers (if present).
        try:
            # Local import to avoid side-effects at module import time
            from . import signals  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive in production
            # Avoid circular import at startup causing crash; log the error if logging is available.
            # We use print here to ensure visibility even if logging isn't configured yet.
            # In real deployments, prefer django logger.
            msg = f"apps.accounts: failed to import signals module: {exc!r}"
            try:
                # Try to use Django logging if available
                import logging

                logging.getLogger(__name__).warning(msg)
            except Exception:
                # Fallback to stdout
                print(msg)
