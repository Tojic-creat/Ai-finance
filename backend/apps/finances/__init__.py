# backend/apps/finances/__init__.py
"""
Package initializer for the `finances` app.

Responsibilities:
- Provide `default_app_config` for compatibility with older Django versions
  (harmless on modern Django where AppConfig auto-discovery is used).
- Attempt to import `signals` and `tasks` (if present) so that signal handlers
  and Celery task registrations are executed when the app is imported.
  Import errors are caught to avoid breaking project startup during early development.
"""

# For Django < 3.2 compatibility; harmless on newer versions.
default_app_config = "apps.finances.apps.FinancesConfig"

# Import signals and tasks if they exist to ensure handlers are registered.
# Wrapped in try/except to avoid hard failures during development.
try:
    from . import signals  # noqa: F401
except Exception:
    # signals may not be implemented yet; skip quietly.
    pass

try:
    # If you have celery tasks in tasks.py, importing them ensures they are discovered.
    from . import tasks  # noqa: F401
except Exception:
    # tasks module optional; skip on import errors.
    pass
