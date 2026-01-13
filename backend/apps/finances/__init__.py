# backend/apps/finances/__init__.py
"""
Package initializer for the `finances` app.

This module attempts to import optional submodules (signals, tasks) only if they exist,
so that application startup does not fail when those files are not present during early dev.
"""

# Keep default_app_config only if you rely on old-style explicit config references.
default_app_config = "apps.finances.apps.FinancesConfig"

# Import optional modules only if present to avoid noisy ImportError logs.
import importlib
import importlib.util
import logging

logger = logging.getLogger(__name__)

_pkg = __package__  # 'apps.finances'

def _maybe_import(submodule_name: str) -> None:
    full_name = f"{_pkg}.{submodule_name}"
    try:
        if importlib.util.find_spec(full_name) is not None:
            importlib.import_module(full_name)
            logger.debug("Imported %s", full_name)
        else:
            logger.debug("Optional module %s not found; skipping import", full_name)
    except Exception:
        # We deliberately swallow exceptions here to avoid breaking startup if optional modules fail.
        # The error will be visible in debug logs inside those modules when executed directly.
        logger.debug("Import of optional module %s failed; continuing. Exception suppressed.", full_name, exc_info=True)


_maybe_import("signals")
_maybe_import("tasks")
