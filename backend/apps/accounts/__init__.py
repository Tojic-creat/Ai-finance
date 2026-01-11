"""
backend/apps/accounts/__init__.py

Package initialization for the `accounts` app.

Responsibilities:
- Provide `default_app_config` for compatibility with older Django versions
  (harmless on modern Django where AppConfig auto-discovery is used).
- Import `signals` (if present) so that signal handlers are registered when
  Django imports the app. Import is wrapped to avoid hard failure when
  the signals module is not yet implemented.
"""

# For Django < 3.2 compatibility; harmless on newer versions.
default_app_config = "apps.accounts.apps.AccountsConfig"

# Try to import signals to ensure they are registered. This import is optional:
# if `signals.py` does not exist yet, we silently continue (useful during early dev).
try:
    from . import signals  # noqa: F401
except Exception:
    # Avoid noisy failures during import time; signal handlers are optional.
    # If you want to see import errors during development, remove the try/except.
    pass
