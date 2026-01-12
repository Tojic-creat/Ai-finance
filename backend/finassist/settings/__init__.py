"""
backend/finassist/settings/__init__.py

Dynamic settings loader for FinAssist.

Purpose:
- Allow importing the settings package as `finassist.settings` and have it
  delegate to one of the concrete modules: `base`, `dev`, `prod`, `test`, etc.
- Support two common ways of selecting configuration:
    1) Explicit DJANGO_SETTINGS_MODULE like "finassist.settings.dev" or
       "finassist.settings.prod" (this is the usual and recommended approach).
    2) If DJANGO_SETTINGS_MODULE is "finassist.settings" (or not set), pick a
       submodule according to ENV var DJANGO_ENV / ENVIRONMENT (defaults to "dev").
- Import all UPPERCASE names from the chosen submodule into this package namespace
  so Django finds settings like INSTALLED_APPS, DATABASES, etc.

Notes:
- Concrete modules (dev.py / prod.py) should import from .base as needed.
- This loader keeps behavior explicit while providing a convenient fallback
  for local development.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Optional

# Allowed names — useful safety check (you can extend as needed)
_ALLOWED_ENVIRONMENTS = {"base", "dev", "prod", "staging", "test"}

# Convenience: read env vars that may be used to pick settings
DJANGO_SETTINGS_MODULE = os.environ.get("DJANGO_SETTINGS_MODULE", "")
DJANGO_ENV = os.environ.get(
    "DJANGO_ENV") or os.environ.get("ENVIRONMENT") or ""
CI = os.environ.get("CI", "").lower() in ("1", "true")


def _determine_submodule() -> str:
    """
    Decide which settings submodule to import (e.g. 'dev' or 'prod').

    Priority:
    1. If DJANGO_SETTINGS_MODULE explicitly references a submodule
       (e.g. finassist.settings.dev) -> use that submodule.
    2. Else, use DJANGO_ENV / ENVIRONMENT env var if provided.
    3. Default to 'dev' for local development.
    """
    if DJANGO_SETTINGS_MODULE:
        parts = DJANGO_SETTINGS_MODULE.split(".")
        # Typical value: 'finassist.settings.dev' -> take last part
        if len(parts) >= 3 and parts[-2] == "settings":
            return parts[-1]

    if DJANGO_ENV:
        return DJANGO_ENV

    # sensible default for local/dev usage
    return "dev"


def _import_settings(submodule: str) -> None:
    """
    Import all UPPERCASE attributes from finassist.settings.<submodule>
    into the current package namespace.
    """
    pkg = __name__  # 'finassist.settings'
    module_name = f"{pkg}.{submodule}"

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise ImportError(
            f"Could not import settings module '{module_name}': {exc}"
        ) from exc

    # Copy all UPPERCASE names (Django expects settings as module-level UPPERCASE)
    for k, v in vars(module).items():
        if k.isupper():
            globals()[k] = v

    # Optionally expose the actual module object for introspection
    globals()["_SETTINGS_MODULE_LOADED"] = module_name


# Main loader logic
_submodule = _determine_submodule()

# Basic sanity: if submodule is not in allowed set, warn but still try to import.
if _submodule not in _ALLOWED_ENVIRONMENTS and not CI:
    # Print a short helpful message only in interactive/dev environments
    sys.stdout.write(
        f"[finassist.settings] loading '{_submodule}' (not in {_ALLOWED_ENVIRONMENTS})\n"
    )

# Attempt import; if it fails, raise informative error
_import_settings(_submodule)

# End of __init__.py — after this, Django will find settings like INSTALLED_APPS, DATABASES, etc.
